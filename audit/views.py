from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import AuditEvent


@login_required
def audit_history(request):
	events = AuditEvent.objects.filter(user=request.user).order_by(
		"-created_at",
		"-pk",
	)
	page_obj = Paginator(events, 50).get_page(request.GET.get("page"))
	return render(request, "audit/history.html", {"page_obj": page_obj})
