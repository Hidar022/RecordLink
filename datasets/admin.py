from django.contrib import admin

from .models import Dataset, DatasetImport, DatasetRecord


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
    search_fields = ("name", "source_name", "original_filename")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DatasetImport)
class DatasetImportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "dataset",
        "original_filename",
        "status",
        "total_rows",
        "accepted_rows",
        "rejected_rows",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "dataset__name",
        "original_filename",
        "uploaded_by__username",
    )
    readonly_fields = (
        "created_at",
        "started_at",
        "finished_at",
    )


@admin.register(DatasetRecord)
class DatasetRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "dataset",
        "dataset_import",
        "source_row_number",
        "validation_status",
        "created_at",
    )
    list_filter = ("validation_status", "created_at")
    search_fields = (
        "dataset__name",
        "dataset_import__original_filename",
    )
    readonly_fields = ("created_at",)