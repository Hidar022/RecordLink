from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import render

from datasets.models import Dataset
from matching.models import CandidatePair, MatchingJob


@login_required
def dashboard(request):
    datasets = Dataset.objects.filter(uploaded_by=request.user)
    jobs = MatchingJob.objects.filter(user=request.user)
    candidate_pairs = CandidatePair.objects.filter(job__user=request.user)
    dataset_stats = datasets.aggregate(
        total=Count("pk"),
        ready=Count("pk", filter=Q(status=Dataset.Status.READY)),
        pending=Count("pk", filter=Q(status=Dataset.Status.PENDING)),
        records=Sum("record_count"),
    )
    job_stats = jobs.aggregate(
        total=Count("pk"),
        completed=Count("pk", filter=Q(status=MatchingJob.Status.COMPLETED)),
    )
    pair_stats = candidate_pairs.aggregate(
        pending=Count(
            "pk",
            filter=Q(review_status=CandidatePair.ReviewStatus.PENDING),
        ),
        reviewed=Count(
            "pk",
            filter=~Q(review_status=CandidatePair.ReviewStatus.PENDING),
        ),
    )

    context = {
        "total_datasets": dataset_stats["total"],
        "ready_datasets": dataset_stats["ready"],
        "pending_datasets": dataset_stats["pending"],
        "total_records": dataset_stats["records"] or 0,
        "total_jobs": job_stats["total"],
        "completed_jobs": job_stats["completed"],
        "pending_review_pairs": pair_stats["pending"],
        "reviewed_pairs": pair_stats["reviewed"],
        "recent_datasets": datasets[:5],
    }

    return render(request, "core/dashboard.html", context)


def home(request):
    if request.user.is_authenticated:
        from django.shortcuts import redirect

        return redirect("dashboard")

    return render(request, "core/home.html")