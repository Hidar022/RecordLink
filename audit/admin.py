from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "action",
        "user",
        "entity_type",
        "entity_id",
        "created_at",
    )

    list_filter = ("action", "created_at")
    search_fields = (
        "action",
        "description",
        "entity_type",
        "entity_id",
    )

    readonly_fields = (
        "user",
        "action",
        "description",
        "entity_type",
        "entity_id",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False