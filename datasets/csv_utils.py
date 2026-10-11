import csv
import io

from django.core.exceptions import ValidationError


MAX_CSV_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_COLUMNS = 100
PREVIEW_LIMIT = 10


def inspect_csv(uploaded_file):
    """Validate a CSV file and return its headers and preview information."""

    filename = uploaded_file.name or ""

    if not filename.lower().endswith(".csv"):
        raise ValidationError("Please upload a file with a .csv extension.")

    if uploaded_file.size == 0:
        raise ValidationError("The uploaded file is empty.")

    if uploaded_file.size > MAX_CSV_SIZE:
        raise ValidationError("The CSV file must not exceed 5 MB.")

    raw_content = uploaded_file.read()
    uploaded_file.seek(0)

    try:
        content = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            "The CSV must use UTF-8 encoding. Please save it as UTF-8 and try again."
        ) from exc

    reader = csv.reader(io.StringIO(content, newline=""), strict=True)

    try:
        raw_headers = next(reader)
    except StopIteration:
        raise ValidationError("The CSV does not contain a header row.")
    except csv.Error as exc:
        raise ValidationError("The CSV structure is invalid.") from exc

    headers = [header.strip() for header in raw_headers]

    if not headers or not any(headers):
        raise ValidationError("The CSV must contain column headers.")

    if any(not header for header in headers):
        raise ValidationError("Column headers cannot be empty.")

    if len(headers) > MAX_COLUMNS:
        raise ValidationError(
            f"The CSV cannot contain more than {MAX_COLUMNS} columns."
        )

    normalized_headers = [header.casefold() for header in headers]

    if len(normalized_headers) != len(set(normalized_headers)):
        raise ValidationError(
            "Duplicate column headers were found. "
            "Please make each column header unique."
        )

    preview_rows = []
    total_rows = 0
    mismatch_count = 0
    mismatch_examples = []

    try:
        for row in reader:
            # Ignore completely blank rows.
            if not row or not any(value.strip() for value in row):
                continue

            total_rows += 1

            if len(preview_rows) < PREVIEW_LIMIT:
                preview_rows.append(row)

            if len(row) != len(headers):
                mismatch_count += 1

                if len(mismatch_examples) < 10:
                    mismatch_examples.append(
                        {
                            "row_number": total_rows + 1,
                            "expected_columns": len(headers),
                            "actual_columns": len(row),
                        }
                    )

    except csv.Error as exc:
        raise ValidationError(
            "The CSV contains malformed quoting or invalid CSV structure."
        ) from exc

    return {
        "headers": headers,
        "preview_rows": preview_rows,
        "total_rows": total_rows,
        "rows_with_column_mismatch": mismatch_count,
        "mismatch_examples": mismatch_examples,
    }