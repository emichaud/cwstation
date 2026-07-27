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


# --- F-031: pairing hardening ------------------------------------------------


def test_pair_default_slug_is_stable():
    """[F-031 fix 1] The default slug is a stable SHA-256 hash of the target — same target
    → same slug across processes, unlike the old per-process hash()."""
    target = "https://b.example.com/webhooks/in/x/"
    s1 = services.pairing_slug(target)
    s2 = services.pairing_slug(target)
    assert s1 == s2
    assert s1.startswith("paired-")
    # Different target → different slug.
    assert services.pairing_slug("https://c.example.com/in/") != s1


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_is_idempotent():
    """[F-031 fix 1] Re-running pair with the same target updates in place — one endpoint,
    one receiver, no duplicates."""
    target = "https://b.example.com/webhooks/in/x/"
    r1 = services.pair_smallstack(target_url=target, events=["*"])
    r2 = services.pair_smallstack(target_url=target, events=["*.created"])
    assert r1["endpoint_id"] == r2["endpoint_id"]
    assert r1["receiver_id"] == r2["receiver_id"]
    assert WebhookEndpoint.objects.filter(target_url=target).count() == 1
    assert WebhookReceiver.objects.count() == 1
    # Config updated in place (events changed on the re-run).
    ep = WebhookEndpoint.objects.get(pk=r1["endpoint_id"])
    assert ep.event_filter == ["*.created"]
    # Secrets preserved across a re-run that didn't supply new ones.
    assert r1["send_secret"] == r2["send_secret"]
    assert r1["recv_secret"] == r2["recv_secret"]


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_two_secrets_by_default():
    """[F-031 fix 2] Send and recv secrets differ by default."""
    r = services.pair_smallstack(target_url="https://b.example.com/in/")
    ep = WebhookEndpoint.objects.get(pk=r["endpoint_id"])
    rec = WebhookReceiver.objects.get(pk=r["receiver_id"])
    assert r["send_secret"] != r["recv_secret"]
    assert ep.secret == r["send_secret"]
    assert rec.secret == r["recv_secret"]


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_supplied_secrets_honored():
    """[F-031 fix 2] --send-secret / --recv-secret are used verbatim; --secret sets both."""
    r = services.pair_smallstack(
        target_url="https://b.example.com/in/", send_secret="SEND123", recv_secret="RECV456"
    )
    ep = WebhookEndpoint.objects.get(pk=r["endpoint_id"])
    rec = WebhookReceiver.objects.get(pk=r["receiver_id"])
    assert ep.secret == "SEND123"
    assert rec.secret == "RECV456"

    r2 = services.pair_smallstack(target_url="https://d.example.com/in/", secret="BOTH")
    ep2 = WebhookEndpoint.objects.get(pk=r2["endpoint_id"])
    rec2 = WebhookReceiver.objects.get(pk=r2["receiver_id"])
    assert ep2.secret == rec2.secret == "BOTH"


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_mirror_command_swaps_secrets():
    """[F-031 fix 3] The peer mirror command swaps the secrets (peer's send = our recv,
    peer's recv = our send) and targets OUR inbound URL."""
    r = services.pair_smallstack(
        target_url="https://b.example.com/webhooks/in/x/",
        send_secret="OURSEND", recv_secret="OURRECV", slug="peer-b",
    )
    cmd = r["mirror_command"]
    assert "--send-secret OURRECV" in cmd   # peer sends to us with OUR recv secret
    assert "--recv-secret OURSEND" in cmd   # peer verifies our sends with OUR send secret
    assert "--target https://a.example.com/webhooks/in/peer-b/" in cmd  # our inbound URL


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_secret_not_in_audit_or_server_log(caplog):
    """[F-031 fix 3] Configuring a pairing must not emit the raw secret to the server log,
    and the request-activity audit stores no request body/params."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="smallstack.webhooks"):
        r = services.pair_smallstack(
            target_url="https://b.example.com/in/", send_secret="TOPSECRETSEND",
            recv_secret="TOPSECRETRECV",
        )
    # The create path logged nothing containing either secret.
    assert "TOPSECRETSEND" not in caplog.text
    assert "TOPSECRETRECV" not in caplog.text
    # The request-activity audit only stores path/method/status — no field can carry a secret.
    from apps.activity.models import RequestLog

    field_names = {f.name for f in RequestLog._meta.get_fields()}
    assert "body" not in field_names and "params" not in field_names
    assert r["send_secret"] == "TOPSECRETSEND"


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_one_way_creates_no_receiver():
    """[F-031 fix 4] --one-way creates only the outbound endpoint."""
    r = services.pair_smallstack(target_url="https://b.example.com/in/", one_way=True)
    assert r["endpoint_id"] is not None
    assert r["receiver_id"] is None
    assert WebhookReceiver.objects.count() == 0
    assert WebhookEndpoint.objects.count() == 1


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_cli_two_way_output_is_honest():
    """[F-031 fix 4] Two-way CLI output prints the mirror command + 'half 1 of 2', and does
    NOT imply it reached the peer."""
    out = StringIO()
    call_command(
        "webhook", "pair", "--target", "https://b.example.com/webhooks/in/x/",
        "--slug", "peer-b", stdout=out,
    )
    text = out.getvalue()
    assert "HALF 1 of 2" in text
    assert "run THIS on the peer" in text
    assert "sc webhook pair --target https://a.example.com/webhooks/in/peer-b/" in text
    assert "configured our instance only" in text.lower()  # honest: didn't reach the peer


@override_settings(SMALLSTACK_WEBHOOK_ORIGIN="https://a.example.com")
def test_pair_verify_fires_test_delivery():
    """[F-031 fix 4] --verify queues a probe delivery through the paired endpoint."""
    from apps.webhooks.models import WebhookDelivery

    out = StringIO()
    call_command(
        "webhook", "pair", "--target", "https://b.example.com/webhooks/in/x/",
        "--slug", "peer-b", "--verify", "--json", stdout=out,
    )
    import json

    data = json.loads(out.getvalue())
    assert "verify" in data
    d = WebhookDelivery.objects.get(pk=data["verify"]["delivery_id"])
    assert d.event_type == "webhooks.pair.verify"


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
