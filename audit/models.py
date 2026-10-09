from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )

    action = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    entity_type = models.CharField(
        max_length=100,
        blank=True,
    )
    entity_id = models.CharField(
        max_length=100,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} — {self.created_at}"