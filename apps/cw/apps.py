from __future__ import annotations

from django.apps import AppConfig


class CwConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cw"
    verbose_name = "CW Station"

    def ready(self) -> None:
        from apps.smallstack.navigation import nav

        key_icon = (
            '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">'
            '<path d="M3 11h2v2H3zm4-4h2v6H7zm4-3h2v9h-2zm4 3h2v6h-2zm4 4h2v2h-2z"/>'
            '<path d="M2 17h20v2H2z"/></svg>'
        )
        nav.register(
            section="main", label="CW Monitor", url_name="cw-monitor",
            icon_svg=key_icon, auth_required=True, order=10,
            active_prefix="/cw/",
        )
        nav.register(
            section="main", label="Live", url_name="cw-live",
            parent="CW Monitor", auth_required=True, order=0,
        )
        nav.register(
            section="main", label="Simulator", url_name="cw-sim",
            parent="CW Monitor", auth_required=True, order=1,
        )
        nav.register(
            section="main", label="Decode", url_name="cw-decode",
            parent="CW Monitor", auth_required=True, order=2,
        )
        nav.register(
            section="main", label="Send", url_name="cw-send",
            parent="CW Monitor", auth_required=True, order=3,
        )
        nav.register(
            section="main", label="Sessions", url_name="cw/sessions-list",
            parent="CW Monitor", auth_required=True, order=4,
        )
        nav.register(
            section="main", label="Logbook", url_name="cw/log-list",
            parent="CW Monitor", auth_required=True, order=5,
        )
        nav.register(
            section="main", label="Rig Setup", url_name="cw-rig-setup",
            parent="CW Monitor", auth_required=True, order=6,
        )
