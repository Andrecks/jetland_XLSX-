import logging
import time
from random import randint

from django.utils import timezone

from .models import Mailing, MailingStatus

logger = logging.getLogger(__name__)


def send_email(
    mailing_id: int,
    *,
    min_delay: int = 5,
    max_delay: int = 20,
) -> None:
    if min_delay > max_delay:
        raise ValueError("min_delay cannot be greater than max_delay")

    mailing = Mailing.objects.get(pk=mailing_id)
    delay = randint(min_delay, max_delay)

    try:
        time.sleep(delay)

        logger.info(
            "Send EMAIL: external_id=%s user_id=%s email=%s subject=%s message=%s delay=%s",
            mailing.external_id,
            mailing.user_id,
            mailing.email,
            mailing.subject,
            mailing.message,
            delay,
        )

        Mailing.objects.filter(pk=mailing_id).update(
            status=MailingStatus.SENT,
            sent_at=timezone.now(),
            error_message="",
        )
    except Exception as exc:
        logger.exception(
            "Email sending failed: mailing_id=%s error=%s",
            mailing_id,
            exc,
        )
        Mailing.objects.filter(pk=mailing_id).update(
            status=MailingStatus.ERROR,
            error_message=str(exc),
        )
        raise