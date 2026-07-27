"""WebhooksDashboardWidget — a card on the central /smallstack/ dashboard."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.smallstack.displays import DashboardWidget


class WebhooksDashboardWidget(DashboardWidget):
    title = "Webhooks"
    icon = (
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 16.98h-5.99c-1.66 0-3.01-1.34-3.01-3s1.35-3 3.01-3H18"/>'
        '<path d="M8 8.01a3 3 0 1 0 0 6"/></svg>'
    )
    order = 46
    url_name = "webhooks_dashboard"

    def get_data(self, model_class: type | None = None) -> dict[str, Any]:
        from .models import WebhookDelivery, WebhookEndpoint

        active = WebhookEndpoint.objects.filter(enabled=True).count()
        day_ago = timezone.now() - timedelta(hours=24)
        recent = WebhookDelivery.objects.filter(created_at__gte=day_ago)
        dead = recent.filter(status=WebhookDelivery.Status.DEAD).count()
        delivered = recent.filter(status=WebhookDelivery.Status.SUCCESS).count()

        status = "danger" if dead else ("ok" if active else "muted")
        detail = f"{delivered} delivered / 24h"
        if dead:
            detail += f" · {dead} failed"

        return {
            "headline": f"{active} active",
            "detail": detail,
            "status": status,
            "extra": {"active": active, "delivered_24h": delivered, "dead_24h": dead},
        }
