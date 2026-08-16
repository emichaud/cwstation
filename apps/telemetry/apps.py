"""Telemetry app configuration."""

from django.apps import AppConfig


class TelemetryConfig(AppConfig):
    """Database-backed log capture.

    Deliberately does no work in ``ready()``. The log handler is constructed by
    ``dictConfig`` during ``django.setup()`` — earlier than any ``ready()`` runs
    — and starts its writer thread lazily on the first record it receives, once
    the app registry reports ready. Navigation and the staff log viewer arrive
    with the UI phase.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telemetry"
    verbose_name = "Telemetry"
