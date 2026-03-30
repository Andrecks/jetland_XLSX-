from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from mailings.importers import ImportValidationError, ParsedRow, RowError, iter_xlsx_rows
from mailings.models import Mailing, MailingStatus
from mailings.services import send_email

logger = logging.getLogger(__name__)


@dataclass
class ImportStats:
    processed_rows: int = 0
    created_records: int = 0
    skipped_records: int = 0
    error_rows: int = 0


class Command(BaseCommand):
    help = "Import mailings from XLSX file and send emails"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to XLSX file")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="DB insert batch size",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="Number of worker threads for sending emails",
        )
        parser.add_argument(
            "--min-delay",
            type=int,
            default=5,
            help="Minimum send delay in seconds",
        )
        parser.add_argument(
            "--max-delay",
            type=int,
            default=20,
            help="Maximum send delay in seconds",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file_path"])
        batch_size = options["batch_size"]
        workers = options["workers"]
        min_delay = options["min_delay"]
        max_delay = options["max_delay"]

        if not file_path.exists():
            raise CommandError(f"File does not exist: {file_path}")

        if batch_size < 1:
            raise CommandError("--batch-size must be >= 1")

        if workers < 1:
            raise CommandError("--workers must be >= 1")

        if min_delay < 0 or max_delay < 0:
            raise CommandError("Delay values must be >= 0")

        if min_delay > max_delay:
            raise CommandError("--min-delay cannot be greater than --max-delay")

        stats = ImportStats()
        batch: list[ParsedRow] = []
        created_mailing_ids: list[int] = []

        try:
            for item in iter_xlsx_rows(file_path):
                stats.processed_rows += 1

                if isinstance(item, RowError):
                    stats.error_rows += 1
                    logger.warning(
                        "Row %s skipped due to validation error: %s",
                        item.row_number,
                        item.message,
                    )
                    continue

                batch.append(item)

                if len(batch) >= batch_size:
                    created_mailing_ids.extend(
                        self._flush_batch(batch=batch, stats=stats, batch_size=batch_size)
                    )
                    batch.clear()

            if batch:
                created_mailing_ids.extend(
                    self._flush_batch(batch=batch, stats=stats, batch_size=batch_size)
                )

        except ImportValidationError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Import failed: %s", exc)
            raise CommandError(f"Import failed: {exc}") from exc

        if created_mailing_ids:
            self._send_created_mailings(
                mailing_ids=created_mailing_ids,
                workers=workers,
                min_delay=min_delay,
                max_delay=max_delay,
            )

        self.stdout.write(self.style.SUCCESS("Import finished"))
        self.stdout.write(f"Processed rows: {stats.processed_rows}")
        self.stdout.write(f"Created records: {stats.created_records}")
        self.stdout.write(f"Skipped records: {stats.skipped_records}")
        self.stdout.write(f"Error rows: {stats.error_rows}")

    def _flush_batch(
        self,
        *,
        batch: list[ParsedRow],
        stats: ImportStats,
        batch_size: int,
    ) -> list[int]:
        unique_rows: list[ParsedRow] = []
        seen_external_ids: set[str] = set()

        for row in batch:
            if row.external_id in seen_external_ids:
                stats.skipped_records += 1
                logger.warning(
                    "Row %s skipped due to duplicate external_id in current batch: %s",
                    row.row_number,
                    row.external_id,
                )
                continue

            seen_external_ids.add(row.external_id)
            unique_rows.append(row)

        if not unique_rows:
            return []

        external_ids = [row.external_id for row in unique_rows]

        existing_external_ids = set(
            Mailing.objects.filter(external_id__in=external_ids).values_list(
                "external_id",
                flat=True,
            )
        )

        rows_to_create = [
            row for row in unique_rows if row.external_id not in existing_external_ids
        ]

        stats.skipped_records += len(existing_external_ids)

        if not rows_to_create:
            return []

        objects = [
            Mailing(
                external_id=row.external_id,
                user_id=row.user_id,
                email=row.email,
                subject=row.subject,
                message=row.message,
                status=MailingStatus.PENDING,
            )
            for row in rows_to_create
        ]

        try:
            with transaction.atomic():
                Mailing.objects.bulk_create(objects, batch_size=batch_size)
        except IntegrityError:
            logger.warning(
                "bulk_create failed due to integrity conflict, falling back to row-by-row create"
            )
            return self._create_one_by_one(rows_to_create=rows_to_create, stats=stats)

        created_ids = list(
            Mailing.objects.filter(
                external_id__in=[row.external_id for row in rows_to_create]
            ).values_list("id", flat=True)
        )

        stats.created_records += len(created_ids)
        return created_ids

    def _create_one_by_one(
        self,
        *,
        rows_to_create: list[ParsedRow],
        stats: ImportStats,
    ) -> list[int]:
        created_ids: list[int] = []

        for row in rows_to_create:
            try:
                mailing = Mailing.objects.create(
                    external_id=row.external_id,
                    user_id=row.user_id,
                    email=row.email,
                    subject=row.subject,
                    message=row.message,
                    status=MailingStatus.PENDING,
                )
            except IntegrityError:
                stats.skipped_records += 1
                logger.warning(
                    "Row %s skipped due to duplicate external_id during fallback create: %s",
                    row.row_number,
                    row.external_id,
                )
                continue

            created_ids.append(mailing.id)

        stats.created_records += len(created_ids)
        return created_ids

    def _send_created_mailings(
        self,
        *,
        mailing_ids: list[int],
        workers: int,
        min_delay: int,
        max_delay: int,
    ) -> None:
        if workers == 1:
            for mailing_id in mailing_ids:
                self._send_one(
                    mailing_id=mailing_id,
                    min_delay=min_delay,
                    max_delay=max_delay,
                )
            return

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    self._send_one,
                    mailing_id=mailing_id,
                    min_delay=min_delay,
                    max_delay=max_delay,
                )
                for mailing_id in mailing_ids
            ]

            for future in as_completed(futures):
                future.result()

    @staticmethod
    def _send_one(*, mailing_id: int, min_delay: int, max_delay: int) -> None:
        try:
            send_email(
                mailing_id,
                min_delay=min_delay,
                max_delay=max_delay,
            )
        except Exception:
            logger.exception("Failed to send mailing_id=%s", mailing_id)