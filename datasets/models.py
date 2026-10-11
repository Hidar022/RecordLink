from django.conf import settings
from django.db import models


class Dataset(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=150)
    source_name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="datasets",
    )

    original_filename = models.CharField(max_length=255, blank=True)
    record_count = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return self.name


class DatasetImport(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PREVIEWED = "previewed", "Previewed"
        MAPPED = "mapped", "Mapped"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_ERRORS = (
            "completed_with_errors",
            "Completed with Errors",
        )
        FAILED = "failed", "Failed"

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="imports",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dataset_imports",
    )

    source_file = models.FileField(
        upload_to="datasets/imports/%Y/%m/",
    )
    original_filename = models.CharField(max_length=255)

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Maps source CSV headers to RecordLink's standard field names.
    column_mapping = models.JSONField(default=dict, blank=True)

    # Stores import-level validation results and processing information.
    validation_summary = models.JSONField(default=dict, blank=True)

    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    accepted_rows = models.PositiveIntegerField(default=0)
    rejected_rows = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["dataset", "status"]),
            models.Index(fields=["uploaded_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.dataset.name} - Import #{self.pk}"


class DatasetRecord(models.Model):
    class ValidationStatus(models.TextChoices):
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="records",
    )

    dataset_import = models.ForeignKey(
        DatasetImport,
        on_delete=models.CASCADE,
        related_name="records",
    )

    # Original row number in the CSV, including the header row in numbering.
    source_row_number = models.PositiveIntegerField()

    # Original CSV values are preserved without normalization.
    original_data = models.JSONField(default=dict)

    # Standardized values used later by the matching engine.
    normalized_data = models.JSONField(default=dict, blank=True)

    validation_status = models.CharField(
        max_length=10,
        choices=ValidationStatus.choices,
        default=ValidationStatus.VALID,
    )

    # A list of validation issues found in this row.
    validation_errors = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_import", "source_row_number"],
                name="unique_import_source_row",
            ),
        ]
        indexes = [
            models.Index(fields=["dataset", "validation_status"]),
            models.Index(fields=["dataset_import", "validation_status"]),
        ]

    def __str__(self):
        return (
            f"{self.dataset.name} - "
            f"Row {self.source_row_number}"
        )