"""Webhook services — signing, SSRF guard, fan-out, and the retry tick.

Kept mostly free of view/request concerns so each piece is unit-testable in
isolation. The retry tick borrows the scheduler's correctness core verbatim: an
**atomic conditional claim** so two concurrent ticks/workers can never send the
same delivery twice.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import socket
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("smallstack.webhooks")

SIGNATURE_HEADER = "X-SmallStack-Signature"
EVENT_HEADER = "X-SmallStack-Event"
DELIVERY_HEADER = "X-SmallStack-Delivery"
# Stable per-event idempotency key (survives replay) — consumers dedupe on it (F-021).
EVENT_ID_HEADER = "X-SmallStack-Event-Id"
# The delivering SmallStack's origin — a receiver can drop self-originated events (F-020).
ORIGIN_HEADER = "X-SmallStack-Origin"

# Default ceiling for a single retry wait when no explicit setting is configured.
DEFAULT_MAX_BACKOFF = 21600  # 6h — matches the last default backoff step


# ---------------------------------------------------------------------------
# Signing / verification
# ---------------------------------------------------------------------------


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest of ``body`` under ``secret`` (both outbound + inbound)."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_header_value(secret: str, body: bytes) -> str:
    """The value we send in X-SmallStack-Signature (algorithm-prefixed)."""
    return f"sha256={sign(secret, body)}"


def verify(secret: str, body: bytes, provided: str) -> bool:
    """Constant-time check of an inbound signature. Accepts a bare hex digest or
    an ``sha256=<hex>`` prefixed value."""
    if not provided:
        return False
    expected = sign(secret, body)
    candidate = provided.split("=", 1)[1] if provided.startswith("sha256=") else provided
    return hmac.compare_digest(expected, candidate.strip())


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def url_is_allowed(url: str) -> tuple[bool, str]:
    """Return (ok, reason). Blocks non-http(s), private/loopback/link-local hosts
    (unless SMALLSTACK_WEBHOOK_ALLOW_PRIVATE), and — when an allowlist is set —
    any host not under an allowlisted suffix."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False, "URL must be http or https."
    host = parts.hostname
    if not host:
        return False, "URL has no host."

    allowlist = getattr(settings, "SMALLSTACK_WEBHOOK_ALLOWLIST", []) or []
    if allowlist:
        host_l = host.lower()
        if not any(host_l == a or host_l.endswith("." + a) for a in allowlist):
            return False, f"Host {host!r} is not in SMALLSTACK_WEBHOOK_ALLOWLIST."

    if getattr(settings, "SMALLSTACK_WEBHOOK_ALLOW_PRIVATE", False):
        return True, ""

    # Reject private/loopback/link-local targets. Resolve the name so an
    # attacker can't point a public-looking host at 127.0.0.1 / 169.254 / etc.
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        # Unresolvable at save time is not necessarily fatal (DNS may be
        # transient); allow it and let the delivery attempt surface the error.
        return True, ""
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False, (
                f"Host {host!r} resolves to a private/loopback address ({ip}). "
                "Set SMALLSTACK_WEBHOOK_ALLOW_PRIVATE=true to allow (dev only)."
            )
    return True, ""


# ---------------------------------------------------------------------------
# Fan-out (called from signals)
# ---------------------------------------------------------------------------


def fan_out(event_type: str, payload: dict[str, Any], *, event_id: str | None = None) -> int:
    """Create one WebhookDelivery per enabled endpoint whose filter matches, and
    enqueue each. Returns the number of deliveries created. Never raises — a
    fan-out failure must not break the originating model save.

    ``event_id`` (F-021) is the stable per-event idempotency key, shared by every
    delivery for this event and reused by replay.
    """
    if not getattr(settings, "SMALLSTACK_WEBHOOKS_ENABLED", True):
        return 0
    if not getattr(settings, "SMALLSTACK_WEBHOOKS_OUTBOUND", True):
        return 0

    max_attempts = int(getattr(settings, "SMALLSTACK_WEBHOOK_MAX_ATTEMPTS", 5))
    created = 0
    try:
        endpoints = WebhookEndpoint.objects.filter(enabled=True)
        for ep in endpoints:
            if not ep.matches(event_type):
                continue
            delivery = WebhookDelivery.objects.create(
                endpoint=ep,
                event_type=event_type,
                payload=payload,
                event_id=event_id,
                status=WebhookDelivery.Status.PENDING,
                max_attempts=max_attempts,
            )
            _enqueue_delivery(delivery.pk)
            created += 1
    except Exception:  # noqa: BLE001 — never let fan-out break a model save
        logger.exception("webhooks: fan_out failed for %s", event_type)
    return created


def _enqueue_delivery(delivery_id: int) -> None:
    """Enqueue the delivery task (best-effort — a broker hiccup leaves the row
    PENDING for the tick to pick up)."""
    try:
        from .tasks import deliver_webhook

        deliver_webhook.enqueue(delivery_id)
    except Exception:  # noqa: BLE001
        logger.warning("webhooks: could not enqueue delivery %s", delivery_id, exc_info=True)


# ---------------------------------------------------------------------------
# Backoff + retry tick
# ---------------------------------------------------------------------------


def backoff_seconds(attempt: int) -> int:
    """Seconds to wait before ``attempt`` (1-based). Reads the configured schedule,
    clamped to its last entry for attempts beyond the table."""
    schedule = getattr(settings, "SMALLSTACK_WEBHOOK_BACKOFF", None) or [60, 300, 1800, 7200, 21600]
    idx = max(0, min(attempt - 1, len(schedule) - 1))
    return int(schedule[idx])


def max_backoff() -> int:
    """The ceiling for any single retry wait (clamps a hostile ``Retry-After``)."""
    return int(getattr(settings, "SMALLSTACK_WEBHOOK_MAX_BACKOFF", DEFAULT_MAX_BACKOFF))


def clamp_backoff(seconds: int) -> int:
    """Clamp a wait to [0, max_backoff]."""
    return max(0, min(int(seconds), max_backoff()))


def parse_retry_after(value: str | None) -> int | None:
    """Parse a ``Retry-After`` header into delta-seconds. Accepts delta-seconds
    (``"5"``) or an HTTP-date (``"Wed, 21 Oct 2015 07:28:00 GMT"``). Returns None if
    absent/unparseable; a past date clamps to 0. Not clamped to max here — the caller
    clamps when scheduling."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        from datetime import timezone as _tz

        when = when.replace(tzinfo=_tz.utc)
    delta = (when - timezone.now()).total_seconds()
    return max(0, int(delta))


def run_due_deliveries(*, now: datetime | None = None, limit: int = 200) -> int:
    """Re-enqueue every delivery whose retry cursor is due. Returns the count
    claimed. Uses the scheduler's atomic conditional claim so concurrent ticks
    can't double-send."""
    if not getattr(settings, "SMALLSTACK_WEBHOOKS_ENABLED", True):
        return 0
    now = now or timezone.now()
    claimed = 0
    due = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.RETRYING,
        next_attempt_at__isnull=False,
        next_attempt_at__lte=now,
    ).order_by("next_attempt_at")[:limit]
    for delivery in due:
        observed = delivery.next_attempt_at
        won = WebhookDelivery.objects.filter(
            pk=delivery.pk, next_attempt_at=observed
        ).update(status=WebhookDelivery.Status.PENDING, next_attempt_at=None)
        if won:
            _enqueue_delivery(delivery.pk)
            claimed += 1
    return claimed


# ---------------------------------------------------------------------------
# Replay (single + bulk dead-letter) — one code path for every surface
# ---------------------------------------------------------------------------


def replay_delivery(original: WebhookDelivery) -> WebhookDelivery:
    """Re-send a past delivery as a fresh attempt, reusing its payload AND ``event_id``
    (F-021) so a consumer can dedupe an operator replay against the original event."""
    replay = WebhookDelivery.objects.create(
        endpoint=original.endpoint,
        event_type=original.event_type,
        payload=original.payload,
        event_id=original.event_id,
        max_attempts=original.max_attempts,
    )
    _enqueue_delivery(replay.pk)
    return replay


def replay_dead(
    *,
    endpoint_id: int | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[int]:
    """Bulk-replay dead deliveries (F-023). Returns the new delivery ids. The single
    most-requested ops verb for a dead-letter queue: after an outage yields N dead
    deliveries, replay them all instead of one id at a time."""
    qs = WebhookDelivery.objects.filter(status=WebhookDelivery.Status.DEAD).select_related(
        "endpoint"
    )
    if endpoint_id is not None:
        qs = qs.filter(endpoint_id=endpoint_id)
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    qs = qs.order_by("created_at")[:limit]
    return [replay_delivery(d).pk for d in qs]
