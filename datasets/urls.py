from django.urls import path

from . import views

app_name = "datasets"

urlpatterns = [
    path("", views.dataset_list, name="dataset_list"),
    path("upload/", views.dataset_upload, name="dataset_upload"),
    path(
        "imports/<int:import_id>/preview/",
        views.dataset_preview,
        name="dataset_preview",
    ),
    path(
        "imports/<int:import_id>/process/",
        views.dataset_process,
        name="dataset_process",
    ),
]