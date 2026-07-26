"""The `webhook` management command (and its `sc webhook` front)."""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.webhooks.models import WebhookDelivery, WebhookEndpoint

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_real_enqueue():
    with mock.patch("apps.webhooks.services._enqueue_delivery") as m:
        yield m


def _run(*args) -> str:
    out = StringIO()
    call_command("webhook", *args, stdout=out)
    return out.getvalue()


def _endpoint(name="Zapier", enabled=True):
    return WebhookEndpoint.objects.create(
        name=name, target_url="https://hooks.example.com/x", event_filter=["*"], enabled=enabled
    )


def test_status_json_reports_counts():
    ep = _endpoint()
    WebhookDelivery.objects.create(endpoint=ep, event_type="t", payload={}, status="dead")
    out = _run("status", "--json")
    data = json.loads(out)
    assert data["endpoints"] == {"active": 1, "total": 1}
    assert data["deliveries"]["dead"] == 1


def test_list_shows_endpoints():
    _endpoint(name="Zapier")
    out = _run("list")
    assert "Zapier" in out
    assert "[on ]" in out


def test_test_subcommand_queues_delivery(no_real_enqueue):
    ep = _endpoint()
    out = _run("test", ep.name, "--json")
    data = json.loads(out)
    assert data["queued"] is True
    d = WebhookDelivery.objects.get(pk=data["delivery_id"])
    assert d.event_type == "webhooks.test.ping"
    assert d.endpoint_id == ep.pk
    no_real_enqueue.assert_called_once_with(d.pk)


def test_test_subcommand_resolves_by_id(no_real_enqueue):
    ep = _endpoint()
    out = _run("test", str(ep.pk), "--json")
    assert json.loads(out)["endpoint"] == ep.name


def test_test_unknown_endpoint_errors():
    with pytest.raises(CommandError, match="no endpoint"):
        _run("test", "nope")


def test_replay_clones_delivery(no_real_enqueue):
    ep = _endpoint()
    original = WebhookDelivery.objects.create(
        endpoint=ep, event_type="a.b.created", payload={"k": 1}, status="dead"
    )
    out = _run("replay", str(original.pk), "--json")
    data = json.loads(out)
    replay = WebhookDelivery.objects.get(pk=data["delivery_id"])
    assert replay.pk != original.pk
    assert replay.event_type == "a.b.created"
    assert replay.payload == {"k": 1}
    assert replay.status == WebhookDelivery.Status.PENDING


def test_replay_requires_numeric_id():
    with pytest.raises(CommandError, match="delivery id"):
        _run("replay", "abc")


def test_deliveries_filters_by_status():
    ep = _endpoint()
    WebhookDelivery.objects.create(endpoint=ep, event_type="ok", payload={}, status="success")
    WebhookDelivery.objects.create(endpoint=ep, event_type="bad", payload={}, status="dead")
    out = _run("deliveries", "--status", "dead", "--json")
    rows = json.loads(out)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "bad"


def test_tick_claims_due_retries(no_real_enqueue):
    ep = _endpoint()
    WebhookDelivery.objects.create(
        endpoint=ep,
        event_type="t",
        payload={},
        status=WebhookDelivery.Status.RETRYING,
        next_attempt_at=timezone.now() - timedelta(minutes=1),
    )
    out = _run("tick", "--json")
    assert json.loads(out)["claimed"] == 1


def test_unknown_subcommand_errors():
    with pytest.raises(CommandError, match="unknown subcommand"):
        _run("bogus")


def test_sc_webhook_fronts_command(no_real_enqueue):
    """`sc webhook status` routes to the webhook command."""
    _endpoint()
    out = StringIO()
    call_command("sc", "webhook", "status", "--json", stdout=out)
    assert json.loads(out.getvalue())["endpoints"]["total"] == 1


def test_create_via_sc_defaults_enabled():
    """[F-003] Scripted creates use model defaults for omitted fields: `sc new
    webhook` without --enabled yields an ENABLED endpoint (model default True),
    matching what the ORM and the web form's pre-checked checkbox produce. An
    explicit --enabled=false still wins."""
    from django.contrib.auth import get_user_model

    staff = get_user_model().objects.create_user(
        username="wh_staff", password="x", is_staff=True
    )

    call_command(
        "sc", "new", "webhook",
        "--name", "NoEnable", "--target_url", "https://hooks.example.com/y",
        "--user", staff.username, "--json", stdout=StringIO(),
    )
    created = WebhookEndpoint.objects.get(name="NoEnable")
    assert created.enabled is True
    assert created.event_filter == []  # JSONField default survives the form path

    call_command(
        "sc", "new", "webhook",
        "--name", "Disabled", "--target_url", "https://hooks.example.com/z",
        "--enabled=false", "--user", staff.username, "--json", stdout=StringIO(),
    )
    assert WebhookEndpoint.objects.get(name="Disabled").enabled is False


def test_replay_to_disabled_endpoint_prints_note(no_real_enqueue):
    """[F-011] A successful replay/test against a disabled endpoint says so —
    otherwise signal events silently don't deliver after an auto-disable."""
    ep = _endpoint(name="Off", enabled=False)
    d = WebhookDelivery.objects.create(
        endpoint=ep, event_type="t", payload={}, status="dead"
    )
    out = _run("replay", str(d.pk))
    assert "disabled" in out
    assert f"sc set webhook {ep.pk} --enabled=true" in out

    out = _run("test", ep.name)
    assert "disabled" in out

    # machine-readable form carries the flag
    data = json.loads(_run("test", ep.name, "--json"))
    assert data["endpoint_enabled"] is False


def test_enabled_endpoint_no_disabled_note(no_real_enqueue):
    ep = _endpoint(name="On", enabled=True)
    out = _run("test", ep.name)
    assert "disabled" not in out


def test_monitor_inventory_lists_endpoints_and_receivers():
    """[F-010] The status-card drill-down mirrors the api/mcp/search peers."""
    from apps.webhooks.models import WebhookReceiver
    from apps.webhooks.monitors import WebhooksMonitor

    _endpoint(name="Catcher")
    WebhookReceiver.objects.create(name="Stripe", slug="stripe-inventory")
    inv = WebhooksMonitor().inventory()
    assert inv["summary"] == "1 endpoint · 1 receiver"
    labels = {i["label"] for i in inv["items"]}
    assert labels == {"Catcher", "Stripe"}
    assert all(i["url"] for i in inv["items"])
