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

    def ready(self) -> None:
        from apps.smallstack.navigation import nav

        nav.register(
            section="admin",
            label="Logs",
            url_name="telemetry:logs",
            icon_svg='<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M4 5h16v2H4zm0 4h10v2H4zm0 4h16v2H4zm0 4h10v2H4z"/></svg>',  # noqa: E501
            staff_required=True,
            order=15,
        )
