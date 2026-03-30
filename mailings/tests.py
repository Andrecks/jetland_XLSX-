from __future__ import annotations

import os
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from mailings.models import Mailing, MailingStatus
from mailings.services import send_email


class ImportMailingsCommandTests(TestCase):
    def create_xlsx(self, headers, rows) -> str:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(headers)

        for row in rows:
            worksheet.append(row)

        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        file_path = tmp.name
        tmp.close()

        workbook.save(file_path)
        workbook.close()

        def cleanup():
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except PermissionError:
                pass

        self.addCleanup(cleanup)
        return file_path

    @patch("mailings.management.commands.import_mailings.send_email")
    def test_import_command_counts_created_skipped_and_errors(self, send_email_mock):
        def fake_send(mailing_id: int, **kwargs):
            Mailing.objects.filter(pk=mailing_id).update(
                status=MailingStatus.SENT,
                error_message="",
            )

        send_email_mock.side_effect = fake_send

        file_path = self.create_xlsx(
            headers=["external_id", "user_id", "email", "subject", "message"],
            rows=[
                ["ext-1001", 101, "ivan@example.com", "Welcome", "Hello, Ivan!"],
                ["ext-1002", 102, "maria@example.com", "Promo", "Discount for Maria"],
                ["ext-1003", 103, "bad-email", "Broken", "Invalid email"],
                ["ext-1002", 104, "duplicate@example.com", "Duplicate", "Duplicate external_id"],
                ["", 105, "noexternal@example.com", "Missing external_id", "Broken row"],
                ["ext-1004", 106, "petr@example.com", "News", "Latest news"],
            ],
        )

        stdout = StringIO()

        call_command(
            "import_mailings",
            file_path,
            batch_size=2,
            workers=1,
            min_delay=0,
            max_delay=0,
            stdout=stdout,
        )

        output = stdout.getvalue()

        self.assertIn("Processed rows: 6", output)
        self.assertIn("Created records: 3", output)
        self.assertIn("Skipped records: 1", output)
        self.assertIn("Error rows: 2", output)

        self.assertEqual(Mailing.objects.count(), 3)
        self.assertEqual(
            Mailing.objects.filter(status=MailingStatus.SENT).count(),
            3,
        )

    def test_import_command_fails_on_missing_required_headers(self):
        file_path = self.create_xlsx(
            headers=["external_id", "user_id", "email", "subject"],
            rows=[
                ["ext-1001", 101, "ivan@example.com", "Welcome"],
            ],
        )

        with self.assertRaises(CommandError) as exc:
            call_command("import_mailings", file_path)

        self.assertIn("Missing required columns", str(exc.exception))

    @patch("mailings.management.commands.import_mailings.send_email")
    def test_existing_external_id_is_skipped(self, send_email_mock):
        send_email_mock.side_effect = lambda mailing_id, **kwargs: Mailing.objects.filter(
            pk=mailing_id
        ).update(status=MailingStatus.SENT)

        Mailing.objects.create(
            external_id="ext-2001",
            user_id=500,
            email="exists@example.com",
            subject="Already exists",
            message="Existing row",
        )

        file_path = self.create_xlsx(
            headers=["external_id", "user_id", "email", "subject", "message"],
            rows=[
                ["ext-2001", 101, "ivan@example.com", "Welcome", "Hello, Ivan!"],
                ["ext-2002", 102, "maria@example.com", "Promo", "Discount for Maria"],
            ],
        )

        stdout = StringIO()

        call_command(
            "import_mailings",
            file_path,
            batch_size=100,
            workers=1,
            min_delay=0,
            max_delay=0,
            stdout=stdout,
        )

        output = stdout.getvalue()

        self.assertIn("Processed rows: 2", output)
        self.assertIn("Created records: 1", output)
        self.assertIn("Skipped records: 1", output)
        self.assertIn("Error rows: 0", output)

        self.assertEqual(Mailing.objects.count(), 2)


class SendEmailServiceTests(TestCase):
    @patch("mailings.services.time.sleep", return_value=None)
    @patch("mailings.services.randint", return_value=0)
    def test_send_email_marks_mailing_as_sent(self, _randint_mock, _sleep_mock):
        mailing = Mailing.objects.create(
            external_id="ext-service-1",
            user_id=1,
            email="test@example.com",
            subject="Test",
            message="Hello",
            status=MailingStatus.PENDING,
        )

        send_email(mailing.id, min_delay=0, max_delay=0)

        mailing.refresh_from_db()

        self.assertEqual(mailing.status, MailingStatus.SENT)
        self.assertIsNotNone(mailing.sent_at)
        self.assertEqual(mailing.error_message, "")