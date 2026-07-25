"""Background tasks — the outbound HTTP send and the inbound handler dispatch.

The framework has no automatic task retry (db_worker marks a failing result
FAILED and moves on), so ``deliver_webhook`` owns its own retry: on failure it
sets the delivery to ``retrying`` with a ``next_attempt_at`` cursor, and the tick
(services.run_due_deliveries) re-enqueues it when due.

Uses urllib from the stdlib for the POST so the app adds no new dependency.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.db.models import F
from django.tasks import task
from django.utils import timezone

from . import services
from .models import WebhookDelivery, WebhookEndpoint, WebhookReceipt

logger = logging.getLogger("smallstack.webhooks")

# Fields written on every attempt outcome (success / retry / dead).
_ATTEMPT_FIELDS = [
    "attempt",
    "response_status",
    "response_ms",
    "error",
    "status",
    "next_attempt_at",
    "updated_at",
]


@task(queue_name="default")
def deliver_webhook(delivery_id: int) -> dict[str, Any]:
    """POST one WebhookDelivery to its endpoint with an HMAC signature.

    Returns a small status dict (also captured as the DBTaskResult return value).
    """
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
    except WebhookDelivery.DoesNotExist:
        return {"error": f"delivery {delivery_id} not found"}

    if delivery.is_terminal:
        return {"skipped": "already terminal", "status": delivery.status}

    endpoint = delivery.endpoint
    attempt = delivery.attempt + 1
    body = json.dumps(delivery.payload, default=str).encode()

    headers = {
        "Content-Type": "application/json",
        services.EVENT_HEADER: delivery.event_type,
        services.DELIVERY_HEADER: str(delivery.pk),
        services.SIGNATURE_HEADER: services.signature_header_value(endpoint.secret, body),
        "User-Agent": "SmallStack-Webhooks/1.0",
    }
    # Endpoint-supplied static headers, but never let them clobber our signing.
    for k, v in (endpoint.headers or {}).items():
        if k not in headers:
            headers[k] = str(v)

    timeout = int(getattr(settings, "SMALLSTACK_WEBHOOK_TIMEOUT", 10))
    started = time.monotonic()
    status_code: int | None = None
    error = ""

    # Re-check the target against the SSRF guard at send time (config may have
    # tightened since the endpoint was created).
    ok, reason = services.url_is_allowed(endpoint.target_url)
    if not ok:
        error = f"blocked by SSRF guard: {reason}"
    else:
        req = urllib.request.Request(endpoint.target_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — scheme checked
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            status_code = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = str(getattr(exc, "reason", exc))
        except Exception as exc:  # noqa: BLE001
            error = repr(exc)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    succeeded = status_code is not None and 200 <= status_code < 300
    _record_attempt(delivery, attempt, status_code, elapsed_ms, error, succeeded)
    return {
        "delivery": delivery.pk,
        "attempt": attempt,
        "response_status": status_code,
        "success": succeeded,
    }


def _record_attempt(
    delivery: WebhookDelivery,
    attempt: int,
    status_code: int | None,
    elapsed_ms: int,
    error: str,
    succeeded: bool,
) -> None:
    """Persist the attempt outcome and schedule a retry (or retire) on failure."""
    now = timezone.now()
    delivery.attempt = attempt
    delivery.response_status = status_code
    delivery.response_ms = elapsed_ms
    delivery.error = error[:2000]

    if succeeded:
        delivery.status = WebhookDelivery.Status.SUCCESS
        delivery.next_attempt_at = None
        delivery.save(update_fields=_ATTEMPT_FIELDS)
        _bump_endpoint(delivery, ok=True)
        return

    if attempt >= delivery.max_attempts:
        delivery.status = WebhookDelivery.Status.DEAD
        delivery.next_attempt_at = None
        delivery.save(update_fields=_ATTEMPT_FIELDS)
        _bump_endpoint(delivery, ok=False, dead=True)
        _notify_dead(delivery)
        return

    # Schedule the next retry.
    from datetime import timedelta

    delivery.status = WebhookDelivery.Status.RETRYING
    delivery.next_attempt_at = now + timedelta(seconds=services.backoff_seconds(attempt))
    delivery.save(update_fields=_ATTEMPT_FIELDS)
    _bump_endpoint(delivery, ok=False)


def _bump_endpoint(delivery: WebhookDelivery, *, ok: bool, dead: bool = False) -> None:
    """Update the endpoint's rollup counters and auto-disable a persistently
    failing endpoint."""
    now = timezone.now()
    ep_qs = WebhookEndpoint.objects.filter(pk=delivery.endpoint_id)
    if ok:
        ep_qs.update(
            last_delivery_at=now,
            last_status=WebhookEndpoint.Status.SUCCESS,
            total_deliveries=F("total_deliveries") + 1,
            consecutive_failures=0,
        )
        return

    status = WebhookEndpoint.Status.DEAD if dead else WebhookEndpoint.Status.RETRYING
    ep_qs.update(
        last_delivery_at=now,
        last_status=status,
        total_deliveries=F("total_deliveries") + 1,
        consecutive_failures=F("consecutive_failures") + 1,
    )
    threshold = int(getattr(settings, "SMALLSTACK_WEBHOOK_AUTO_DISABLE_AFTER", 20))
    if threshold:
        ep = WebhookEndpoint.objects.filter(pk=delivery.endpoint_id).first()
        if ep and ep.consecutive_failures >= threshold and ep.enabled:
            WebhookEndpoint.objects.filter(pk=ep.pk).update(enabled=False)
            logger.warning(
                "webhooks: endpoint %s auto-disabled after %s consecutive failures",
                ep.name,
                ep.consecutive_failures,
            )


def _notify_dead(delivery: WebhookDelivery) -> None:
    """Email configured recipients when a delivery exhausts its retries."""
    recipients = getattr(settings, "SMALLSTACK_WEBHOOK_FAILURE_EMAILS", None)
    if not recipients:
        return
    try:
        from apps.tasks.tasks import send_email_task

        send_email_task.enqueue(
            recipient=list(recipients),
            subject=f"[webhooks] delivery failed: {delivery.event_type}",
            message=(
                f"Webhook delivery to “{delivery.endpoint.name}” gave up after "
                f"{delivery.attempt} attempts.\n\n"
                f"Event: {delivery.event_type}\n"
                f"Last response: {delivery.response_status}\n"
                f"Error: {delivery.error}\n"
            ),
        )
    except Exception:  # noqa: BLE001
        logger.warning("webhooks: dead-delivery notification could not be enqueued", exc_info=True)


# ---------------------------------------------------------------------------
# Inbound dispatch
# ---------------------------------------------------------------------------


@task(queue_name="default")
def dispatch_incoming(receipt_id: int) -> dict[str, Any]:
    """Run the registered handler for an accepted inbound receipt."""
    try:
        receipt = WebhookReceipt.objects.select_related("receiver").get(pk=receipt_id)
    except WebhookReceipt.DoesNotExist:
        return {"error": f"receipt {receipt_id} not found"}

    from .registry import get_handler

    handler = get_handler(receipt.receiver.handler_name)
    if handler is None:
        receipt.status = WebhookReceipt.Status.FAILED
        receipt.error = f"no handler registered for {receipt.receiver.handler_name!r}"
        receipt.save(update_fields=["status", "error"])
        return {"error": receipt.error}

    try:
        handler(receipt)
    except Exception as exc:  # noqa: BLE001 — a handler error is recorded, not fatal
        receipt.status = WebhookReceipt.Status.FAILED
        receipt.error = repr(exc)[:2000]
        receipt.save(update_fields=["status", "error"])
        logger.exception("webhooks: handler %s failed", receipt.receiver.handler_name)
        return {"error": receipt.error}

    receipt.status = WebhookReceipt.Status.PROCESSED
    receipt.save(update_fields=["status"])
    return {"receipt": receipt.pk, "status": "processed"}
