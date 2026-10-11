import csv
import io
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .models import Dataset, DatasetImport, DatasetRecord


logger = logging.getLogger(__name__)

IDENTITY_FIELDS = {
    "full_name",
    "first_name",
    "last_name",
    "date_of_birth",
    "phone",
    "email",
    "hospital_record_id",
    "national_id",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
)

SEX_VALUES = {
    "m": "male",
    "male": "male",
    "f": "female",
    "female": "female",
    "other": "other",
    "unknown": "unknown",
}


def clean_text(value):
    """Standardize Unicode and whitespace without changing the source data."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.split())


def normalize_date(value):
    """Convert supported date formats to ISO format."""
    value = clean_text(value)

    if not value:
        return ""

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue

    return value


def normalize_field(field_name, value):
    """Normalize a mapped field for later record comparison."""
    value = clean_text(value)

    if not value:
        return ""

    if field_name == "date_of_birth":
        return normalize_date(value)

    if field_name == "email":
        return value.casefold()

    if field_name == "phone":
        # Preserve leading zeroes and an initial plus sign.
        value = re.sub(r"[^\d+]", "", value)

        if "+" in value[1:]:
            value = value.replace("+", "")

        return value

    if field_name == "sex":
        return SEX_VALUES.get(value.casefold(), value.casefold())

    # Names, addresses, and identifiers are compared case-insensitively.
    return value.casefold()


def validate_mapped_fields(normalized_data):
    """Return validation issues without discarding the original record."""
    errors = []

    identity_values = [
        normalized_data.get(field_name, "")
        for field_name in IDENTITY_FIELDS
    ]

    if not any(identity_values):
        errors.append(
            "At least one identity field must contain a value."
        )

    date_of_birth = normalized_data.get("date_of_birth", "")
    if date_of_birth:
        try:
            datetime.strptime(date_of_birth, "%Y-%m-%d")
        except ValueError:
            errors.append(
                "Date of birth must be a valid date."
            )

    email = normalized_data.get("email", "")
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors.append("Email address is invalid.")

    return errors


def build_record_data(headers, row, mapping):
    """Build original and normalized dictionaries for one CSV row."""
    original_data = {
        header: row[index] if index < len(row) else ""
        for index, header in enumerate(headers)
    }

    # Preserve extra cells rather than silently throwing them away.
    if len(row) > len(headers):
        original_data["_extra_columns"] = row[len(headers):]

    normalized_data = {}

    for standard_field, source_header in mapping.items():
        source_value = original_data.get(source_header, "")
        normalized_data[standard_field] = normalize_field(
            standard_field,
            source_value,
        )

    return original_data, normalized_data


def process_dataset_import(import_id, user):
    """
    Validate, normalize, and persist every non-empty row in a dataset import.

    Reprocessing replaces this import's previous records rather than
    appending duplicate rows.
    """
    with transaction.atomic():
        dataset_import = (
            DatasetImport.objects.select_for_update()
            .select_related("dataset")
            .get(
                pk=import_id,
                uploaded_by=user,
                dataset__uploaded_by=user,
            )
        )

        if dataset_import.status == DatasetImport.Status.PROCESSING:
            raise ValidationError(
                "This dataset import is already being processed."
            )

        mapping = dataset_import.column_mapping

        if not mapping:
            raise ValidationError(
                "Save a column mapping before processing this dataset."
            )

        if not IDENTITY_FIELDS.intersection(mapping):
            raise ValidationError(
                "The column mapping must include an identity field."
            )

        if not dataset_import.source_file:
            raise ValidationError(
                "The uploaded CSV file is unavailable."
            )

        dataset_import.status = DatasetImport.Status.PROCESSING
        dataset_import.started_at = timezone.now()
        dataset_import.finished_at = None
        dataset_import.error_message = ""
        dataset_import.processed_rows = 0
        dataset_import.accepted_rows = 0
        dataset_import.rejected_rows = 0
        dataset_import.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "error_message",
                "processed_rows",
                "accepted_rows",
                "rejected_rows",
            ]
        )

    try:
        with transaction.atomic():
            dataset_import = (
                DatasetImport.objects.select_for_update()
                .select_related("dataset")
                .get(pk=import_id)
            )
            dataset = Dataset.objects.select_for_update().get(
                pk=dataset_import.dataset_id
            )

            # Makes a retry safe: replace records belonging to this import.
            DatasetRecord.objects.filter(
                dataset_import=dataset_import
            ).delete()

            with dataset_import.source_file.open("rb") as source:
                try:
                    content = source.read().decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValidationError(
                        "The CSV must use UTF-8 encoding."
                    ) from exc

            reader = csv.reader(
                io.StringIO(content, newline=""),
                strict=True,
            )

            try:
                headers = [header.strip() for header in next(reader)]
            except StopIteration as exc:
                raise ValidationError(
                    "The CSV does not contain a header row."
                ) from exc

            missing_headers = set(mapping.values()) - set(headers)

            if missing_headers:
                raise ValidationError(
                    "The saved column mapping refers to missing CSV columns."
                )

            processed_rows = 0
            accepted_rows = 0
            rejected_rows = 0
            validation_counts = Counter()
            invalid_examples = []
            records_to_create = []

            for source_row_number, row in enumerate(reader, start=2):
                # Skip rows that are entirely blank.
                if not row or not any(value.strip() for value in row):
                    continue

                processed_rows += 1

                original_data, normalized_data = build_record_data(
                    headers,
                    row,
                    mapping,
                )

                errors = []

                if len(row) != len(headers):
                    errors.append(
                        "Row has "
                        f"{len(row)} columns; expected {len(headers)}."
                    )

                errors.extend(
                    validate_mapped_fields(normalized_data)
                )

                validation_status = (
                    DatasetRecord.ValidationStatus.INVALID
                    if errors
                    else DatasetRecord.ValidationStatus.VALID
                )

                if errors:
                    rejected_rows += 1

                    for error in errors:
                        validation_counts[error] += 1

                    if len(invalid_examples) < 20:
                        invalid_examples.append(
                            {
                                "source_row_number": source_row_number,
                                "errors": errors,
                            }
                        )
                else:
                    accepted_rows += 1

                records_to_create.append(
                    DatasetRecord(
                        dataset=dataset,
                        dataset_import=dataset_import,
                        source_row_number=source_row_number,
                        original_data=original_data,
                        normalized_data=normalized_data,
                        validation_status=validation_status,
                        validation_errors=errors,
                    )
                )

                if len(records_to_create) >= 500:
                    DatasetRecord.objects.bulk_create(
                        records_to_create,
                        batch_size=500,
                    )
                    records_to_create.clear()

            if records_to_create:
                DatasetRecord.objects.bulk_create(
                    records_to_create,
                    batch_size=500,
                )

            dataset_import.processed_rows = processed_rows
            dataset_import.accepted_rows = accepted_rows
            dataset_import.rejected_rows = rejected_rows
            dataset_import.status = (
                DatasetImport.Status.COMPLETED_WITH_ERRORS
                if rejected_rows
                else DatasetImport.Status.COMPLETED
            )
            dataset_import.finished_at = timezone.now()
            dataset_import.validation_summary = {
                **dataset_import.validation_summary,
                "processed_rows": processed_rows,
                "accepted_rows": accepted_rows,
                "rejected_rows": rejected_rows,
                "validation_error_counts": dict(validation_counts),
                "invalid_examples": invalid_examples,
            }
            dataset_import.save(
                update_fields=[
                    "processed_rows",
                    "accepted_rows",
                    "rejected_rows",
                    "status",
                    "finished_at",
                    "validation_summary",
                ]
            )

            # Record count represents records eligible for matching.
            dataset.record_count = accepted_rows
            dataset.status = Dataset.Status.READY
            dataset.save(
                update_fields=[
                    "record_count",
                    "status",
                    "updated_at",
                ]
            )

            return dataset_import

    except Exception:
        logger.exception(
            "Processing failed for dataset import %s",
            import_id,
        )

        DatasetImport.objects.filter(pk=import_id).update(
            status=DatasetImport.Status.FAILED,
            error_message=(
                "The import could not be processed. "
                "Check the file and mapping, then retry."
            ),
            finished_at=timezone.now(),
        )

        raise