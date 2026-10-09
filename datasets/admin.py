from django.contrib import admin

from .models import Dataset


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source_name",
        "uploaded_by",
        "record_count",
        "status",
        "created_at",
    )

    list_filter = ("status", "created_at")
    search_fields = (
        "name",
        "source_name",
        "original_filename",
    )

    readonly_fields = ("created_at", "updated_at")