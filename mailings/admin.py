from django.contrib import admin

from .models import Mailing


@admin.register(Mailing)
class MailingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "external_id",
        "user_id",
        "email",
        "status",
        "created_at",
        "sent_at",
    )
    search_fields = ("external_id", "email", "user_id")
    list_filter = ("status", "created_at", "sent_at")