"""Webhooks status monitor — are deliveries getting through?

Two failure signals, mirroring the scheduler monitor's shape: a backlog of
overdue retries (the delivery tick isn't firing / is wedged) trips DOWN, and a
high recent-failure ratio (with a minimum sample) also trips DOWN.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.smallstack.monitors import CheckResult, Monitor, Service

SERVICE_KEY = "webhooks"


class WebhooksService(Service):
    key = SERVICE_KEY
    title = "Webhooks"
    description = "Outbound event delivery"
    category = "core"
    order = 45
    icon = (
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 16.98h-5.99c-1.66 0-3.01-1.34-3.01-3s1.35-3 3.01-3H18"/>'
        '<path d="M8 8.01a3 3 0 1 0 0 6"/></svg>'
    )
    detail_url_name = "webhooks_dashboard"


class WebhooksMonitor(Monitor):
    key = "webhooks-delivery"
    service = SERVICE_KEY
    title = "Webhook delivery healthy"
    order = 10
    detail_url_name = "webhooks_dashboard"

    def _grace(self) -> int:
        # A retry overdue by more than this ⇒ the delivery tick isn't firing.
        return int(getattr(settings, "SMALLSTACK_SCHEDULER_OVERDUE_GRACE_SECONDS", 300))

    def _min_sample(self) -> int:
        return int(getattr(settings, "SMALLSTACK_SCHEDULER_FAILURE_MIN_SAMPLE", 5))

    def check(self) -> CheckResult:
        from .models import WebhookDelivery, WebhookEndpoint

        now = timezone.now()
        overdue = WebhookDelivery.objects.filter(
            status=WebhookDelivery.Status.RETRYING,
            next_attempt_at__isnull=False,
            next_attempt_at__lt=now - timedelta(seconds=self._grace()),
        ).count()
        if overdue:
            return CheckResult.down(note=f"{overdue} retry(ies) overdue — delivery tick not firing?")

        recent = WebhookDelivery.objects.filter(created_at__gte=now - timedelta(hours=1))
        total = recent.count()
        dead = recent.filter(status=WebhookDelivery.Status.DEAD).count()
        if total >= self._min_sample() and dead / total > 0.5:
            return CheckResult.down(note=f"{dead}/{total} deliveries gave up in the last hour")

        active = WebhookEndpoint.objects.filter(enabled=True).count()
        return CheckResult.up(note=f"{active} active endpoint(s)")
