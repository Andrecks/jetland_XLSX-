from django.db import models


class MailingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    ERROR = "error", "Error"


class Mailing(models.Model):
    external_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="External ID",
        help_text="Уникальный идентификатор записи во внешней системе",
    )
    user_id = models.PositiveBigIntegerField(
        verbose_name="User ID",
        help_text="Идентификатор пользователя",
    )
    email = models.EmailField(
        verbose_name="Email",
        help_text="Email получателя",
    )
    subject = models.CharField(
        max_length=255,
        verbose_name="Subject",
        help_text="Тема письма",
    )
    message = models.TextField(
        verbose_name="Message",
        help_text="Текст письма",
    )
    status = models.CharField(
        max_length=20,
        choices=MailingStatus.choices,
        default=MailingStatus.PENDING,
        verbose_name="Status",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        verbose_name="Error message",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mailings"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} -> {self.email}"