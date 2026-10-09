from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from datasets.models import Dataset


@login_required
def dashboard(request):
    datasets = Dataset.objects.filter(
        uploaded_by=request.user
    )

    context = {
        "total_datasets": datasets.count(),
        "ready_datasets": datasets.filter(
            status=Dataset.Status.READY
        ).count(),
        "pending_datasets": datasets.filter(
            status=Dataset.Status.PENDING
        ).count(),
        "failed_datasets": datasets.filter(
            status=Dataset.Status.FAILED
        ).count(),
        "total_records": sum(
            datasets.values_list("record_count", flat=True)
        ),
        "recent_datasets": datasets[:5],
    }

    return render(request, "core/dashboard.html", context)


def home(request):
    if request.user.is_authenticated:
        from django.shortcuts import redirect

        return redirect("dashboard")

    from django.shortcuts import redirect

    return redirect("login")