"""SmallStack↔SmallStack pairing (F-027) + the backward-compatible envelope upgrade
(F-014). The flagship path: one step stands up a loop-safe two-way link."""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.webhooks import services
from apps.webhooks.models import WebhookEndpoint, WebhookReceiver
from apps.webhooks.views import WebhookReceiverCRUDView

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_real_enqueue():
    with mock.patch("apps.webhooks.services._enqueue_delivery"):
        yield


# --- F-027 pairing -----------------------------------------------------------


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_creates_loop_safe_endpoint_and_receiver():
    result = services.pair_smallstack(
        target_url="https://b.example.com/webhooks/in/paired-a/", events=["*"]
    )
    ep = WebhookEndpoint.objects.get(pk=result["endpoint_id"])
    rec = WebhookReceiver.objects.get(pk=result["receiver_id"])

    # Endpoint → peer, smallstack transform, matching events.
    assert ep.target_url == "https://b.example.com/webhooks/in/paired-a/"
    assert ep.transform == "smallstack"
    assert ep.event_filter == ["*"]
    assert ep.enabled is True

    # Receiver ← peer, loop guard on: ignore our own origin, verify signatures.
    assert rec.verifier == "hmac"
    assert rec.require_signature is True
    assert rec.ignore_origin == "https://a.example.com"

    # One shared secret across both sides.
    assert ep.secret == rec.secret == result["secret"]


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_cli():
    out = StringIO()
    call_command(
        "webhook", "pair", "--target", "https://b.example.com/webhooks/in/x/",
        "--slug", "peer-b", "--json", stdout=out,
    )
    import json

    data = json.loads(out.getvalue())
    assert data["receiver_slug"] == "peer-b"
    assert data["origin"] == "https://a.example.com"
    assert WebhookEndpoint.objects.filter(pk=data["endpoint_id"]).exists()
    assert WebhookReceiver.objects.filter(slug="peer-b").exists()


# --- F-014 envelope upgrade (backward-compatible) ----------------------------


@pytest.fixture
def receiver_emits_webhooks():
    original = WebhookReceiverCRUDView.enable_webhooks
    WebhookReceiverCRUDView.enable_webhooks = True
    WebhookReceiverCRUDView.webhook_events = None
    try:
        yield
    finally:
        WebhookReceiverCRUDView.enable_webhooks = original


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_envelope_has_new_and_old_keys(receiver_emits_webhooks):
    """The upgraded envelope keeps every original key AND adds event_id / origin /
    resource with an absolute url."""
    WebhookEndpoint.objects.create(
        name="e", target_url="https://hooks.example.com/x", event_filter=["*"]
    )
    r = WebhookReceiver.objects.create(name="R", slug="r")
    from apps.webhooks.models import WebhookDelivery

    d = WebhookDelivery.objects.filter(event_type="webhooks.webhookreceiver.created").first()
    assert d is not None
    p = d.payload

    # Original keys unchanged (existing consumers keep working).
    for key in ("event", "action", "model", "id", "occurred_at", "data"):
        assert key in p
    assert p["event"] == "webhooks.webhookreceiver.created"

    # New keys added.
    assert p["event_id"]  # a UUID string
    assert p["origin"] == "https://a.example.com"
    assert p["resource"]["type"] == "webhooks.webhookreceiver"
    assert p["resource"]["id"] == r.pk
    # Absolute resource URL a consumer can act on without guessing the path.
    assert p["resource"]["url"].startswith("https://a.example.com/smallstack/api/")
    assert p["resource"]["url"].endswith(f"/{r.pk}/")

    # event_id on the payload == event_id stamped on the delivery row (F-021).
    assert str(d.event_id) == p["event_id"]
