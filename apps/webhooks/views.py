"""Webhook views — CRUDViews (admin+REST+MCP+search), the dashboard, the inbound
receiver endpoint, the delivery tick, and the test/replay/reveal actions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.db.models import F
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.smallstack.crud import Action, CRUDView
from apps.smallstack.mixins import StaffRequiredMixin

from . import services
from .models import WebhookDelivery, WebhookEndpoint, WebhookReceipt, WebhookReceiver

LOCALHOST_IPS = {"127.0.0.1", "::1"}


def _status_color(value: str | None) -> str:
    from django.utils.html import format_html

    color = {
        "success": "var(--success-fg)",
        "pending": "var(--body-quiet-color)",
        "retrying": "var(--warning-fg)",
        "failed": "var(--error-fg)",
        "dead": "var(--error-fg)",
        "accepted": "var(--body-quiet-color)",
        "processed": "var(--success-fg)",
        "rejected": "var(--error-fg)",
    }.get(value or "", "var(--body-quiet-color)")
    return format_html('<span style="color: {};">{}</span>', color, value or "—")


def _delivery_status(value: Any, obj: Any) -> Any:
    return _status_color(obj.status)


def _endpoint_status(value: Any, obj: Any) -> Any:
    return _status_color(obj.last_status or "")


def _receipt_status(value: Any, obj: Any) -> Any:
    return _status_color(obj.status)


# ---------------------------------------------------------------------------
# CRUDViews — the free admin + REST + MCP + search surfaces
# ---------------------------------------------------------------------------


class WebhookEndpointCRUDView(CRUDView):
    """Outbound endpoints — operator- and agent-managed, so REST + MCP are on.

    ``secret`` is deliberately excluded from every serialized surface (list,
    detail, API, MCP): it's auto-generated and only revealed through the staff-only
    reveal action. Only ``secret_preview`` is ever shown.
    """

    model = WebhookEndpoint
    fields = ["name", "target_url", "event_filter", "headers", "enabled"]
    list_fields = ["name", "target_url", "enabled", "last_status", "total_deliveries"]
    detail_fields = [
        "name",
        "target_url",
        "event_filter",
        "headers",
        "enabled",
        "last_status",
        "total_deliveries",
        "consecutive_failures",
        "last_delivery_at",
    ]
    link_field = "name"
    field_transforms = {"last_status": _endpoint_status}
    url_base = "webhooks/endpoints"
    paginate_by = 25
    mixins = [StaffRequiredMixin]

    enable_api = True
    api_extra_fields = ["last_status", "total_deliveries", "consecutive_failures", "last_delivery_at"]

    enable_mcp = True
    mcp_description = (
        "an outbound webhook endpoint — a URL that receives a signed POST when a "
        "subscribed model event fires. event_filter holds fnmatch patterns over "
        "'<app>.<model>.<action>' (e.g. 'scheduler.scheduledjob.*')."
    )
    mcp_singular = "webhook"
    mcp_plural = "webhooks"

    enable_search = True
    search_fields = ["name", "target_url"]
    search_display = "name"
    search_subtitle = "target_url"


class WebhookDeliveryCRUDView(CRUDView):
    """Read-only outbound delivery history."""

    model = WebhookDelivery
    fields = ["endpoint", "event_type", "status", "attempt", "response_status", "created_at"]
    list_fields = ["endpoint", "event_type", "status", "attempt", "response_status", "created_at"]
    detail_fields = [
        "endpoint",
        "event_type",
        "payload",
        "status",
        "attempt",
        "max_attempts",
        "next_attempt_at",
        "response_status",
        "response_ms",
        "error",
        "task_result_id",
        "created_at",
    ]
    link_field = "event_type"
    field_transforms = {"status": _delivery_status}
    url_base = "webhooks/deliveries"
    paginate_by = 30
    mixins = [StaffRequiredMixin]
    actions = [Action.LIST, Action.DETAIL]

    enable_api = True
    api_expand_fields = ["endpoint"]


class WebhookReceiverCRUDView(CRUDView):
    """Inbound receivers — external systems POST to /webhooks/in/<slug>/."""

    model = WebhookReceiver
    fields = ["name", "slug", "handler", "signature_header", "require_signature", "enabled"]
    list_fields = ["name", "slug", "handler", "enabled", "total_received"]
    detail_fields = [
        "name",
        "slug",
        "handler",
        "signature_header",
        "require_signature",
        "enabled",
        "total_received",
        "last_received_at",
    ]
    link_field = "name"
    url_base = "webhooks/receivers"
    paginate_by = 25
    mixins = [StaffRequiredMixin]

    enable_api = True
    api_extra_fields = ["total_received", "last_received_at"]

    enable_mcp = True
    mcp_description = (
        "an inbound webhook receiver — a slug-addressed endpoint at "
        "/webhooks/in/<slug>/ that verifies a signature and runs a registered handler."
    )
    mcp_singular = "webhook_receiver"
    mcp_plural = "webhook_receivers"


class WebhookReceiptCRUDView(CRUDView):
    """Read-only inbound receipt history."""

    model = WebhookReceipt
    fields = ["receiver", "status", "verified", "source_ip", "received_at"]
    list_fields = ["receiver", "status", "verified", "source_ip", "received_at"]
    detail_fields = [
        "receiver",
        "status",
        "verified",
        "source_ip",
        "headers",
        "body",
        "error",
        "task_result_id",
        "received_at",
    ]
    link_field = "received_at"
    field_transforms = {"status": _receipt_status}
    url_base = "webhooks/receipts"
    paginate_by = 30
    mixins = [StaffRequiredMixin]
    actions = [Action.LIST, Action.DETAIL]

    enable_api = True
    api_expand_fields = ["receiver"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class WebhooksDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "webhooks/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        day_ago = now - timedelta(hours=24)
        deliveries = WebhookDelivery.objects.filter(created_at__gte=day_ago)

        ctx["active_endpoints"] = WebhookEndpoint.objects.filter(enabled=True).count()
        ctx["total_endpoints"] = WebhookEndpoint.objects.count()
        ctx["delivered_24h"] = deliveries.filter(status=WebhookDelivery.Status.SUCCESS).count()
        ctx["retrying"] = WebhookDelivery.objects.filter(
            status=WebhookDelivery.Status.RETRYING
        ).count()
        ctx["dead_24h"] = deliveries.filter(status=WebhookDelivery.Status.DEAD).count()
        ctx["recent_deliveries"] = (
            WebhookDelivery.objects.select_related("endpoint").order_by("-created_at")[:15]
        )
        ctx["active_receivers"] = WebhookReceiver.objects.filter(enabled=True).count()
        ctx["recent_receipts"] = (
            WebhookReceipt.objects.select_related("receiver").order_by("-received_at")[:10]
        )
        return ctx


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@require_POST
def test_endpoint(request: HttpRequest, pk: int) -> HttpResponse:
    """Fire a sample signed delivery to an endpoint so an operator can confirm
    reachability without waiting for a real event."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse(status=403)
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_type="webhooks.test.ping",
        payload={
            "event": "webhooks.test.ping",
            "action": "test",
            "occurred_at": timezone.now().isoformat(),
            "data": {"message": "This is a test delivery from SmallStack."},
        },
        max_attempts=1,
    )
    services._enqueue_delivery(delivery.pk)
    messages.success(request, f"Test delivery queued for “{endpoint.name}”.")
    return redirect("webhooks/endpoints-detail", pk=pk)


@require_POST
def replay_delivery(request: HttpRequest, pk: int) -> HttpResponse:
    """Re-send a past delivery (e.g. one that died) as a fresh attempt."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse(status=403)
    original = get_object_or_404(WebhookDelivery.objects.select_related("endpoint"), pk=pk)
    replay = WebhookDelivery.objects.create(
        endpoint=original.endpoint,
        event_type=original.event_type,
        payload=original.payload,
        max_attempts=original.max_attempts,
    )
    services._enqueue_delivery(replay.pk)
    messages.success(request, f"Replayed delivery #{original.pk} as #{replay.pk}.")
    return redirect("webhooks/deliveries-detail", pk=replay.pk)


@require_POST
def reveal_secret(request: HttpRequest, pk: int) -> JsonResponse:
    """Return an endpoint's full signing secret (staff-only, on demand)."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return JsonResponse({"error": "forbidden"}, status=403)
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    return JsonResponse({"secret": endpoint.secret})


@require_POST
def rotate_secret(request: HttpRequest, pk: int) -> HttpResponse:
    """Generate a new signing secret for an endpoint (invalidates the old one)."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse(status=403)
    from .models import generate_secret

    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    endpoint.secret = generate_secret()
    endpoint.save(update_fields=["secret", "updated_at"])
    messages.success(request, f"Signing secret rotated for “{endpoint.name}”.")
    return redirect("webhooks/endpoints-detail", pk=pk)


# ---------------------------------------------------------------------------
# Delivery tick (localhost, mirrors scheduler_tick)
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def webhooks_tick(request: HttpRequest) -> JsonResponse:
    """Localhost-only endpoint for cron to drive the retry tick inside gunicorn.

    Mirrors scheduler_tick / heartbeat_ping. Use exactly one trigger per
    deployment (this, the run_due_deliveries command, or fold into scheduler_beat).
    """
    if request.META.get("REMOTE_ADDR", "") not in LOCALHOST_IPS:
        return JsonResponse({"error": "forbidden"}, status=403)
    claimed = services.run_due_deliveries()
    return JsonResponse({"claimed": claimed})


# ---------------------------------------------------------------------------
# Inbound receiver — the public endpoint external systems POST to
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def incoming_webhook(request: HttpRequest, slug: str) -> JsonResponse:
    """Receive an external POST, verify its signature, record a receipt, and queue
    the registered handler. Returns 202 fast; 401 on bad signature; 404 for an
    unknown/disabled receiver."""
    if not getattr(settings, "SMALLSTACK_WEBHOOKS_ENABLED", True) or not getattr(
        settings, "SMALLSTACK_WEBHOOKS_INBOUND", True
    ):
        return JsonResponse({"error": "inbound webhooks disabled"}, status=404)

    receiver = WebhookReceiver.objects.filter(slug=slug, enabled=True).first()
    if receiver is None:
        return JsonResponse({"error": "no such receiver"}, status=404)

    raw = request.body
    provided = request.headers.get(receiver.signature_header, "")
    verified = services.verify(receiver.secret, raw, provided)

    # Snapshot a safe subset of headers (never store cookies/authorization raw).
    safe_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"cookie", "authorization"}
    }

    if receiver.require_signature and not verified:
        WebhookReceipt.objects.create(
            receiver=receiver,
            source_ip=request.META.get("REMOTE_ADDR"),
            headers=safe_headers,
            body=raw.decode("utf-8", "replace")[:100_000],
            verified=False,
            status=WebhookReceipt.Status.REJECTED,
            error="signature verification failed",
        )
        return JsonResponse({"error": "invalid signature"}, status=401)

    receipt = WebhookReceipt.objects.create(
        receiver=receiver,
        source_ip=request.META.get("REMOTE_ADDR"),
        headers=safe_headers,
        body=raw.decode("utf-8", "replace")[:100_000],
        verified=verified,
        status=WebhookReceipt.Status.ACCEPTED,
    )
    WebhookReceiver.objects.filter(pk=receiver.pk).update(
        last_received_at=timezone.now(),
        total_received=F("total_received") + 1,
    )

    try:
        from .tasks import dispatch_incoming

        dispatch_incoming.enqueue(receipt.pk)
    except Exception:  # noqa: BLE001 — a broker hiccup shouldn't 500 the sender
        pass
    return JsonResponse({"accepted": True, "receipt": receipt.pk}, status=202)
