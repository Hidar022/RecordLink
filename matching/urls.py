from django.urls import path

from . import views

app_name = "matching"

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("create/", views.job_create, name="job_create"),
    path("review/", views.review_queue, name="review_queue"),
    path("evaluation/", views.evaluation_list, name="evaluation_list"),
    path(
        "candidates/<int:pair_id>/review/",
        views.review_decision,
        name="review_decision",
    ),
    path(
        "<int:pk>/export.csv",
        views.export_job_csv,
        name="export_job_csv",
    ),
    path(
        "<int:pk>/evaluation/",
        views.evaluation_detail,
        name="evaluation_detail",
    ),
    path(
        "<int:pk>/candidates/<int:pair_id>/label/",
        views.save_reference_label,
        name="save_reference_label",
    ),
    path(
        "<int:job_id>/candidates/<int:pair_id>/",
        views.candidate_detail,
        name="candidate_detail",
    ),
    path("<int:pk>/", views.job_detail, name="job_detail"),
]