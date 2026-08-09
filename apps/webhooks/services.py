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


# ---------------------------------------------------------------------------
# SmallStack ↔ SmallStack pairing (F-027)
# ---------------------------------------------------------------------------


def available_events() -> list[str]:
    """Every concrete event type a model with ``enable_webhooks`` can emit, plus the
    common wildcards — the option list for the event-filter picker (F-015)."""
    try:
        from apps.smallstack.crud import CRUDView
    except Exception:  # noqa: BLE001
        return ["*"]
    all_actions = ("created", "updated", "deleted")
    events: list[str] = []
    for view in CRUDView._registry.values():
        if not getattr(view, "enable_webhooks", False):
            continue
        model = getattr(view, "model", None)
        if model is None:
            continue
        label = f"{model._meta.app_label}.{model._meta.model_name}"
        actions = getattr(view, "webhook_events", None) or all_actions
        events.append(f"{label}.*")
        for action in actions:
            events.append(f"{label}.{action}")
    # Handy wildcards first.
    return ["*", "*.created", "*.updated", "*.deleted", *sorted(set(events))]


def pairing_slug(target_url: str) -> str:
    """A **stable** default receiver slug for a pairing to ``target_url``.

    Uses a SHA-256 digest (not Python's per-process-salted ``hash()``) so re-running
    ``pair`` with the same target yields the **same** slug every time — the precondition
    for idempotent re-runs (F-031)."""
    digest = hashlib.sha256(target_url.encode()).hexdigest()[:8]
    return f"paired-{digest}"


def pair_smallstack(
    *,
    target_url: str,
    events: list[str] | None = None,
    name: str | None = None,
    slug: str | None = None,
    send_secret: str | None = None,
    recv_secret: str | None = None,
    secret: str | None = None,
    one_way: bool = False,
    owner: Any | None = None,
) -> dict[str, Any]:
    """Configure **this** SmallStack's half of a loop-safe SmallStack↔SmallStack link (F-027/F-031).

    This only ever touches the local instance — it does **not** reach the peer. It creates
    (or updates, idempotently, keyed on a stable ``slug``):

    * an **endpoint** → ``target_url`` (``transform="smallstack"``, matching ``events``),
      signing outbound A→B with the **send secret**, and
    * unless ``one_way``, a **receiver** verifying inbound B→A with the **recv secret**
      (``verifier="hmac"``, ``ignore_origin`` = our own origin so an echoed event is dropped).

    **Two secrets, not one** (F-031): the direction we send and the direction we receive use
    independent secrets, so a compromise of one direction doesn't expose the other. ``secret``
    is a convenience alias that sets both to the same value; ``send_secret`` / ``recv_secret``
    set them independently. Any omitted secret is generated.

    **Idempotent:** re-running with the same target (hence the same stable ``slug``) updates
    the existing endpoint+receiver in place rather than minting duplicates. Existing secrets
    are preserved unless explicitly supplied.

    Returns the ids, both secrets, our origin + inbound URL, and — crucially — the exact
    **mirror command** the operator runs on the peer, with the secrets already SWAPPED
    (the peer's send = our recv, the peer's recv = our send).
    """
    from .context import current_origin
    from .models import WebhookReceiver, generate_secret

    events = events or ["*"]
    origin = current_origin()
    base = name or f"SmallStack {target_url}"
    receiver_slug = slug or pairing_slug(target_url)

    # Resolve the two secrets. `secret` aliases both; otherwise each defaults independently.
    resolved_send = send_secret or secret
    resolved_recv = recv_secret or secret

    # --- outbound endpoint (A→B), idempotent among PAIRED objects only -------------------
    # Keyed on (target_url, is_paired=True): a re-pair adopts the paired endpoint it
    # created, but a hand-made endpoint to the same URL (is_paired=False) is never touched.
    endpoint_defaults: dict[str, Any] = {
        "name": f"{base} (out)",
        "event_filter": events,
        "transform": "smallstack",
        "auth_scheme": "hmac",
        "enabled": True,
        "owner": owner,
    }
    endpoint, ep_created = WebhookEndpoint.objects.get_or_create(
        target_url=target_url,
        is_paired=True,
        defaults={**endpoint_defaults, "secret": resolved_send or generate_secret()},
    )
    if not ep_created:
        # Update config in place; only overwrite the secret when one was supplied.
        endpoint.name = endpoint_defaults["name"]
        endpoint.event_filter = events
        endpoint.transform = "smallstack"
        endpoint.auth_scheme = "hmac"
        endpoint.enabled = True
        if resolved_send:
            endpoint.secret = resolved_send
        endpoint.save()
    send_value = endpoint.secret

    receiver = None
    recv_value = resolved_recv or generate_secret()
    if not one_way:
        # Slug is globally unique: if a hand-made (non-paired) receiver already holds our
        # default slug, suffix ours so we create a distinct PAIRED receiver alongside it
        # rather than colliding or adopting theirs. An explicit --slug is honored as-is.
        if slug is None and WebhookReceiver.objects.filter(
            slug=receiver_slug, is_paired=False
        ).exists():
            receiver_slug = f"{receiver_slug}-p"
        # Keyed on (slug, is_paired=True): same strictness — a hand-made receiver on the
        # same slug is not adopted.
        receiver_defaults: dict[str, Any] = {
            "name": f"{base} (in)",
            "verifier": "hmac",
            "require_signature": True,
            "ignore_origin": origin,  # drop our own events echoed back — the loop guard
            "enabled": True,
            "owner": owner,
        }
        receiver, rec_created = WebhookReceiver.objects.get_or_create(
            slug=receiver_slug,
            is_paired=True,
            defaults={**receiver_defaults, "secret": recv_value},
        )
        if not rec_created:
            receiver.name = receiver_defaults["name"]
            receiver.verifier = "hmac"
            receiver.require_signature = True
            receiver.ignore_origin = origin
            receiver.enabled = True
            if resolved_recv:
                receiver.secret = resolved_recv
            receiver.save()
        recv_value = receiver.secret

    inbound_url = f"/webhooks/in/{receiver_slug}/"
    our_inbound_absolute = (
        f"{origin.rstrip('/')}{inbound_url}" if origin.startswith(("http://", "https://")) else inbound_url
    )

    # The mirror command for the peer, with secrets SWAPPED: the peer sends to US using our
    # recv secret, and verifies OUR sends using our send secret.
    import json as _json

    mirror_command = (
        "sc webhook pair"
        f" --target {our_inbound_absolute}"
        f" --send-secret {recv_value}"
        f" --recv-secret {send_value}"
        f" --events '{_json.dumps(events)}'"
    )

    return {
        "endpoint_id": endpoint.pk,
        "receiver_id": receiver.pk if receiver else None,
        "receiver_slug": receiver_slug if not one_way else None,
        "inbound_url": inbound_url if not one_way else None,
        "inbound_url_absolute": our_inbound_absolute if not one_way else None,
        "send_secret": send_value,
        "recv_secret": recv_value,
        "origin": origin,
        "events": events,
        "one_way": one_way,
        "endpoint_created": ep_created,
        "mirror_command": mirror_command,
    }


def pair_verify(endpoint: WebhookEndpoint) -> dict[str, Any]:
    """Fire a signed test delivery through a paired endpoint to check the round-trip (F-031).

    Returns the created delivery id; the operator (or ``--json`` consumer) then inspects the
    delivery status to see whether the peer accepted it. This is a *live* probe — it only
    confirms the local→peer direction reached the peer (a 2xx); the reverse direction is
    proven by the peer's own verify."""
    from django.utils import timezone

    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_type="webhooks.pair.verify",
        payload={
            "event": "webhooks.pair.verify",
            "action": "verify",
            "occurred_at": timezone.now().isoformat(),
            "data": {"message": "SmallStack pairing round-trip check."},
        },
        max_attempts=1,  # a probe, not a retry demo
    )
    _enqueue_delivery(delivery.pk)
    return {"delivery_id": delivery.pk, "endpoint_id": endpoint.pk, "endpoint": endpoint.name}
