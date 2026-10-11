import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from audit.models import AuditEvent
from datasets.models import Dataset
from .engine import execute_matching_job
from .evaluation import calculate_evaluation_metrics
from .models import CandidatePair, MatchingJob, ReferenceLabel


@login_required
def job_list(request):
    jobs = (
        MatchingJob.objects.filter(user=request.user)
        .select_related("left_dataset", "right_dataset")
        .order_by("-created_at")
    )

    return render(
        request,
        "matching/job_list.html",
        {"jobs": jobs},
    )


@login_required
def job_create(request):
    ready_datasets = Dataset.objects.filter(
        uploaded_by=request.user,
        status=Dataset.Status.READY,
    ).order_by("name")

    if ready_datasets.count() < 2:
        messages.warning(
            request,
            "You need at least two ready datasets to run matching.",
        )

    if request.method == "POST":
        left_id = request.POST.get("left_dataset")
        right_id = request.POST.get("right_dataset")

        left_dataset = ready_datasets.filter(pk=left_id).first()
        right_dataset = ready_datasets.filter(pk=right_id).first()

        if not left_dataset or not right_dataset:
            messages.error(
                request,
                "Select two ready datasets that belong to your account.",
            )
            return redirect("matching:job_create")

        if left_dataset.pk == right_dataset.pk:
            messages.error(
                request,
                "Choose two different datasets.",
            )
            return redirect("matching:job_create")

        try:
            match_threshold = float(
                request.POST.get("match_threshold", "0.82")
            )
            review_threshold = float(
                request.POST.get("review_threshold", "0.55")
            )
        except (TypeError, ValueError):
            messages.error(request, "Enter valid numeric thresholds.")
            return redirect("matching:job_create")

        if not (
            0 <= review_threshold < match_threshold <= 1
        ):
            messages.error(
                request,
                "Thresholds must satisfy 0 ≤ review threshold "
                "< match threshold ≤ 1.",
            )
            return redirect("matching:job_create")

        job = MatchingJob.objects.create(
            user=request.user,
            left_dataset=left_dataset,
            right_dataset=right_dataset,
            match_threshold=match_threshold,
            review_threshold=review_threshold,
            configuration={
                "scoring_method": "weighted_field_similarity",
                "candidate_generation": "blocking",
                "score_scale": "0_to_1",
            },
        )
        AuditEvent.objects.create(
            user=request.user,
            action="matching_job_created",
            description=f"Matching job #{job.pk} created.",
            entity_type="matching_job",
            entity_id=str(job.pk),
        )

        try:
            job = execute_matching_job(job)
        except Exception:
            messages.error(
                request,
                f"Matching job #{job.pk} could not be completed.",
            )
        else:
            if job.status == MatchingJob.Status.FAILED:
                messages.error(
                    request,
                    f"Matching job #{job.pk} failed: {job.error_message}",
                )
            else:
                messages.success(
                    request,
                    f"Matching job #{job.pk} finished successfully.",
                )

        if job.status in {
            MatchingJob.Status.COMPLETED,
            MatchingJob.Status.FAILED,
        }:
            outcome = job.status
            AuditEvent.objects.create(
                user=request.user,
                action=f"matching_job_{outcome}",
                description=f"Matching job #{job.pk} {outcome}.",
                entity_type="matching_job",
                entity_id=str(job.pk),
            )

        return redirect("matching:job_detail", pk=job.pk)

    return render(
        request,
        "matching/job_create.html",
        {"datasets": ready_datasets},
    )


@login_required
def job_detail(request, pk):
    job = get_object_or_404(
        MatchingJob.objects.select_related(
            "left_dataset",
            "right_dataset",
        ),
        pk=pk,
        user=request.user,
    )

    classification = request.GET.get("classification", "")
    allowed_classifications = {
        value for value, _label in CandidatePair.Classification.choices
    }

    candidates = job.candidate_pairs.select_related(
        "record_a",
        "record_b",
    )

    if classification in allowed_classifications:
        candidates = candidates.filter(classification=classification)

    candidates = candidates.order_by("-score", "pk")

    paginator = Paginator(candidates, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "matching/job_detail.html",
        {
            "job": job,
            "page_obj": page_obj,
            "classification_choices": CandidatePair.Classification.choices,
            "selected_classification": classification,
        },
    )


@login_required
def review_queue(request):
    jobs = MatchingJob.objects.filter(user=request.user).select_related(
        "left_dataset",
        "right_dataset",
    )
    candidates = CandidatePair.objects.filter(
        job__user=request.user,
    ).select_related(
        "job__left_dataset",
        "job__right_dataset",
        "record_a__dataset",
        "record_b__dataset",
        "reviewed_by",
    )

    selected_job = request.GET.get("job", "")
    if selected_job:
        if selected_job.isdecimal():
            candidates = candidates.filter(job_id=int(selected_job))
        else:
            candidates = candidates.none()

    classification = request.GET.get("classification", "")
    allowed_classifications = {
        value for value, _label in CandidatePair.Classification.choices
    }
    if classification in allowed_classifications:
        candidates = candidates.filter(classification=classification)
    else:
        classification = ""

    review_status = request.GET.get(
        "review_status",
        CandidatePair.ReviewStatus.PENDING,
    )
    allowed_review_statuses = {
        value for value, _label in CandidatePair.ReviewStatus.choices
    }
    if review_status in allowed_review_statuses:
        candidates = candidates.filter(review_status=review_status)
    else:
        review_status = ""

    paginator = Paginator(candidates.order_by("-score", "pk"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "matching/review_queue.html",
        {
            "page_obj": page_obj,
            "jobs": jobs,
            "classification_choices": CandidatePair.Classification.choices,
            "review_status_choices": CandidatePair.ReviewStatus.choices,
            "selected_job": selected_job,
            "selected_classification": classification,
            "selected_review_status": review_status,
        },
    )


@login_required
def candidate_detail(request, job_id, pair_id):
    job = get_object_or_404(
        MatchingJob.objects.select_related("left_dataset", "right_dataset"),
        pk=job_id,
        user=request.user,
    )
    pair = get_object_or_404(
        CandidatePair.objects.select_related(
            "record_a__dataset",
            "record_b__dataset",
            "reviewed_by",
        ),
        pk=pair_id,
        job=job,
    )
    reference_label = ReferenceLabel.objects.filter(
        candidate_pair=pair,
    ).select_related("labelled_by").first()

    return render(
        request,
        "matching/candidate_detail.html",
        {
            "job": job,
            "pair": pair,
            "reference_label": reference_label,
        },
    )


@login_required
@require_POST
def review_decision(request, pair_id):
    decision = request.POST.get("decision")
    valid_decisions = {
        CandidatePair.ReviewStatus.CONFIRMED_MATCH,
        CandidatePair.ReviewStatus.CONFIRMED_NON_MATCH,
    }

    with transaction.atomic():
        pair = get_object_or_404(
            CandidatePair.objects.select_for_update().select_related("job"),
            pk=pair_id,
            job__user=request.user,
        )

        if decision not in valid_decisions:
            messages.error(request, "Choose a valid review decision.")
        elif pair.review_status == decision:
            messages.info(request, "That review decision is already recorded.")
        else:
            pair.review_status = decision
            pair.reviewed_by = request.user
            pair.reviewed_at = timezone.now()
            pair.save(
                update_fields=["review_status", "reviewed_by", "reviewed_at"]
            )
            action = (
                "candidate_pair_confirmed_match"
                if decision == CandidatePair.ReviewStatus.CONFIRMED_MATCH
                else "candidate_pair_confirmed_non_match"
            )
            AuditEvent.objects.create(
                user=request.user,
                action=action,
                description=(
                    f"Candidate pair #{pair.pk} in matching job "
                    f"#{pair.job_id} was reviewed."
                ),
                entity_type="candidate_pair",
                entity_id=str(pair.pk),
            )
            messages.success(request, "Review decision recorded.")

    fallback_url = reverse_candidate_detail(pair.job_id, pair.pk)
    return_to = request.POST.get("return_to", "")
    if (
        return_to
        and url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return redirect(return_to)
    return redirect(fallback_url)


def reverse_candidate_detail(job_id, pair_id):
    from django.urls import reverse

    return reverse(
        "matching:candidate_detail",
        kwargs={"job_id": job_id, "pair_id": pair_id},
    )


def safe_csv_cell(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(
        ("=", "+", "-", "@")
    ):
        return f"'{value}"
    return value


@login_required
def export_job_csv(request, pk):
    job = get_object_or_404(
        MatchingJob.objects.select_related("left_dataset", "right_dataset"),
        pk=pk,
        user=request.user,
    )
    candidates = CandidatePair.objects.filter(job=job).select_related(
        "record_a",
        "record_b",
        "reviewed_by",
    )

    classification = request.GET.get("classification", "")
    allowed_classifications = {
        value for value, _label in CandidatePair.Classification.choices
    }
    if classification in allowed_classifications:
        candidates = candidates.filter(classification=classification)

    review_status = request.GET.get("review_status", "")
    allowed_review_statuses = {
        value for value, _label in CandidatePair.ReviewStatus.choices
    }
    if review_status in allowed_review_statuses:
        candidates = candidates.filter(review_status=review_status)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="matching-job-{job.pk}-candidates.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "job_id",
        "left_dataset",
        "right_dataset",
        "record_a_id",
        "record_a_row",
        "record_b_id",
        "record_b_row",
        "score",
        "classification",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "comparison_details",
    ])

    for pair in candidates.order_by("pk"):
        writer.writerow([
            pair.job_id,
            safe_csv_cell(job.left_dataset.name),
            safe_csv_cell(job.right_dataset.name),
            pair.record_a_id,
            pair.record_a.source_row_number,
            pair.record_b_id,
            pair.record_b.source_row_number,
            f"{pair.score:.6f}",
            pair.classification,
            pair.review_status,
            safe_csv_cell(pair.reviewed_by.get_username())
            if pair.reviewed_by_id
            else "",
            pair.reviewed_at.isoformat() if pair.reviewed_at else "",
            safe_csv_cell(json.dumps(
                pair.comparison_details,
                ensure_ascii=False,
                sort_keys=True,
            )),
        ])

    AuditEvent.objects.create(
        user=request.user,
        action="matching_job_csv_exported",
        description=f"CSV export generated for matching job #{job.pk}.",
        entity_type="matching_job",
        entity_id=str(job.pk),
    )
    return response


@login_required
def evaluation_list(request):
    jobs = MatchingJob.objects.filter(user=request.user).select_related(
        "left_dataset",
        "right_dataset",
    )
    return render(
        request,
        "matching/evaluation_list.html",
        {"jobs": jobs},
    )


@login_required
def evaluation_detail(request, pk):
    job = get_object_or_404(
        MatchingJob.objects.select_related("left_dataset", "right_dataset"),
        pk=pk,
        user=request.user,
    )
    metrics = calculate_evaluation_metrics(
        job.candidate_pairs.filter(
            reference_label__isnull=False,
        ).values_list("classification", "reference_label__label")
    )
    candidates = job.candidate_pairs.select_related(
        "record_a__dataset",
        "record_b__dataset",
        "reference_label",
    ).order_by("pk")
    page_obj = Paginator(candidates, 25).get_page(request.GET.get("page"))

    return render(
        request,
        "matching/evaluation_detail.html",
        {"job": job, "metrics": metrics, "page_obj": page_obj},
    )


@login_required
@require_POST
def save_reference_label(request, pk, pair_id):
    job = get_object_or_404(MatchingJob, pk=pk, user=request.user)
    pair = get_object_or_404(CandidatePair, pk=pair_id, job=job)
    label = request.POST.get("label")
    valid_labels = {value for value, _text in ReferenceLabel.Label.choices}

    if label not in valid_labels:
        messages.error(request, "Choose a valid reference label.")
        return redirect("matching:evaluation_detail", pk=job.pk)

    with transaction.atomic():
        reference_label, created = ReferenceLabel.objects.update_or_create(
            candidate_pair=pair,
            defaults={"label": label, "labelled_by": request.user},
        )
        AuditEvent.objects.create(
            user=request.user,
            action=(
                "reference_label_created"
                if created
                else "reference_label_updated"
            ),
            description=(
                f"A reference label was {'created' if created else 'updated'} "
                f"for candidate pair #{pair.pk}."
            ),
            entity_type="candidate_pair",
            entity_id=str(pair.pk),
        )

    messages.success(request, "Reference label saved.")
    return redirect("matching:evaluation_detail", pk=job.pk)