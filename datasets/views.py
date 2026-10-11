from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .csv_utils import inspect_csv
from .forms import DatasetMappingForm, DatasetUploadForm
from .models import Dataset, DatasetImport

import logging

from django.views.decorators.http import require_POST

from .record_processing import process_dataset_import

logger = logging.getLogger(__name__)

@login_required
def dataset_upload(request):
    if request.method == "POST":
        form = DatasetUploadForm(request.POST, request.FILES)

        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]
            csv_info = form.csv_info

            try:
                with transaction.atomic():
                    dataset = Dataset.objects.create(
                        name=form.cleaned_data["name"],
                        source_name=form.cleaned_data["source_name"],
                        description=form.cleaned_data["description"],
                        uploaded_by=request.user,
                        original_filename=uploaded_file.name[:255],
                        status=Dataset.Status.PENDING,
                    )

                    dataset_import = DatasetImport.objects.create(
                        dataset=dataset,
                        uploaded_by=request.user,
                        source_file=uploaded_file,
                        original_filename=uploaded_file.name[:255],
                        status=DatasetImport.Status.PREVIEWED,
                        total_rows=csv_info["total_rows"],
                        validation_summary={
                            "headers": csv_info["headers"],
                            "rows_with_column_mismatch": (
                                csv_info["rows_with_column_mismatch"]
                            ),
                            "mismatch_examples": csv_info["mismatch_examples"],
                        },
                        finished_at=timezone.now(),
                    )

            except Exception:
                # Log the underlying exception in production.
                form.add_error(
                    None,
                    "The dataset could not be saved. Please try again.",
                )
            else:
                return redirect(
                    "datasets:dataset_preview",
                     import_id=dataset_import.pk,
            )

    else:
        form = DatasetUploadForm()

    return render(
        request,
        "datasets/upload.html",
        {"form": form},
    )


@login_required
def dataset_preview(request, import_id):
    dataset_import = get_object_or_404(
        DatasetImport.objects.select_related("dataset"),
        pk=import_id,
        uploaded_by=request.user,
        dataset__uploaded_by=request.user,
    )

    try:
        csv_info = inspect_csv(dataset_import.source_file)
    except ValidationError:
        return render(
            request,
            "datasets/preview.html",
            {
                "dataset_import": dataset_import,
                "csv_error": (
                    "The uploaded file could not be read. "
                    "Please upload the file again."
                ),
            },
            status=422,
        )

    if request.method == "POST":
        form = DatasetMappingForm(
            request.POST,
            headers=csv_info["headers"],
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    dataset_import.column_mapping = form.get_mapping()
                    dataset_import.status = DatasetImport.Status.MAPPED
                    dataset_import.validation_summary = {
                        **dataset_import.validation_summary,
                        "headers": csv_info["headers"],
                        "rows_with_column_mismatch": (
                            csv_info["rows_with_column_mismatch"]
                        ),
                        "mismatch_examples": csv_info["mismatch_examples"],
                    }
                    dataset_import.save(
                        update_fields=[
                            "column_mapping",
                            "status",
                            "validation_summary",
                        ]
                    )
            except Exception:
                # Keep implementation details out of the user-facing response.
                form.add_error(
                    None,
                    "The column mapping could not be saved. Please try again.",
                )
            else:
                messages.success(
                    request,
                    "Column mapping saved successfully.",
                )
                return redirect(
                    "datasets:dataset_preview",
                    import_id=dataset_import.pk,
                )
    else:
        form = DatasetMappingForm(
            headers=csv_info["headers"],
            initial=dataset_import.column_mapping,
        )

    return render(
        request,
        "datasets/preview.html",
        {
            "dataset_import": dataset_import,
            "headers": csv_info["headers"],
            "preview_rows": csv_info["preview_rows"],
            "total_rows": csv_info["total_rows"],
            "rows_with_column_mismatch": (
                csv_info["rows_with_column_mismatch"]
            ),
            "mismatch_examples": csv_info["mismatch_examples"],
            "mapping_form": form,
        },
    )

@login_required
@require_POST
def dataset_process(request, import_id):
    dataset_import = get_object_or_404(
        DatasetImport,
        pk=import_id,
        uploaded_by=request.user,
        dataset__uploaded_by=request.user,
    )

    try:
        processed_import = process_dataset_import(
            import_id=dataset_import.pk,
            user=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        logger.exception(
            "Unable to process dataset import %s",
            dataset_import.pk,
        )
        messages.error(
            request,
            "Dataset processing failed. Please review the import and try again.",
        )
    else:
        messages.success(
            request,
            (
                f"Import processed successfully: "
                f"{processed_import.accepted_rows} valid records and "
                f"{processed_import.rejected_rows} invalid records."
            ),
        )

    return redirect(
        "datasets:dataset_preview",
        import_id=dataset_import.pk,
    )

@login_required
def dataset_list(request):
    datasets = (
        Dataset.objects.filter(uploaded_by=request.user)
        .prefetch_related("imports")
        .order_by("-created_at")
    )

    context = {
        "datasets": datasets,
        "total_datasets": datasets.count(),
    }

    return render(request, "datasets/list.html", context)