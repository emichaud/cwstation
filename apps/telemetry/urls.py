"""URL configuration for the telemetry app."""

from django.urls import path

from .views import CaptureControlView, LogDetailView, LogListView

app_name = "telemetry"

urlpatterns = [
    path("", LogListView.as_view(), name="logs"),
    path("capture/", CaptureControlView.as_view(), name="capture"),
    path("<int:pk>/", LogDetailView.as_view(), name="log_detail"),
]
