"""The global save/delete observer fans out to matching endpoints.

Uses WebhookReceiverCRUDView as the guinea pig model: we flip its enable_webhooks
on for the test (and restore it), so creating/updating/deleting a WebhookReceiver
emits webhooks.webhookreceiver.<action>. Delivery enqueue is patched to a no-op so
no HTTP happens — we assert only the fan-out (delivery-row creation).
"""

from __future__ import annotations

from unittest import mock

import pytest

from apps.webhooks.models import WebhookDelivery, WebhookEndpoint, WebhookReceiver
from apps.webhooks.views import WebhookReceiverCRUDView

pytestmark = pytest.mark.django_db


@pytest.fixture
def receiver_emits_webhooks():
    """Turn on outbound webhooks for the WebhookReceiver model for one test."""
    original = WebhookReceiverCRUDView.enable_webhooks
    original_events = WebhookReceiverCRUDView.webhook_events
    WebhookReceiverCRUDView.enable_webhooks = True
    WebhookReceiverCRUDView.webhook_events = None
    try:
        yield
    finally:
        WebhookReceiverCRUDView.enable_webhooks = original
        WebhookReceiverCRUDView.webhook_events = original_events


@pytest.fixture(autouse=True)
def no_real_enqueue():
    """Never actually run the delivery task in these fan-out tests."""
    with mock.patch("apps.webhooks.services._enqueue_delivery"):
        yield


def _endpoint(patterns):
    return WebhookEndpoint.objects.create(
        name="test", target_url="https://hooks.example.com/x", event_filter=patterns
    )


def test_create_fires_matching_endpoint(receiver_emits_webhooks):
    _endpoint(["webhooks.webhookreceiver.*"])
    WebhookReceiver.objects.create(name="R", slug="r")

    deliveries = WebhookDelivery.objects.all()
    assert deliveries.count() == 1
    d = deliveries.first()
    assert d.event_type == "webhooks.webhookreceiver.created"
    assert d.payload["action"] == "created"
    assert d.payload["data"]["id"] == d.payload["id"]


def test_update_and_delete_fire(receiver_emits_webhooks):
    _endpoint(["*"])
    r = WebhookReceiver.objects.create(name="R", slug="r")
    r.name = "R2"
    r.save()
    r_id = r.pk
    r.delete()

    events = sorted(WebhookDelivery.objects.values_list("event_type", flat=True))
    assert events == [
        "webhooks.webhookreceiver.created",
        "webhooks.webhookreceiver.deleted",
        "webhooks.webhookreceiver.updated",
    ]
    deleted = WebhookDelivery.objects.get(event_type="webhooks.webhookreceiver.deleted")
    assert deleted.payload["id"] == r_id


def test_non_matching_filter_gets_nothing(receiver_emits_webhooks):
    _endpoint(["scheduler.scheduledjob.*"])  # unrelated model
    WebhookReceiver.objects.create(name="R", slug="r")
    assert WebhookDelivery.objects.count() == 0


def test_disabled_endpoint_gets_nothing(receiver_emits_webhooks):
    ep = _endpoint(["*"])
    ep.enabled = False
    ep.save()
    WebhookReceiver.objects.create(name="R", slug="r")
    assert WebhookDelivery.objects.count() == 0


def test_model_without_opt_in_fires_nothing():
    """No enable_webhooks ⇒ no fan-out even with a catch-all endpoint."""
    _endpoint(["*"])
    WebhookReceiver.objects.create(name="R", slug="r")
    assert WebhookDelivery.objects.count() == 0


def test_webhook_events_restriction(receiver_emits_webhooks):
    WebhookReceiverCRUDView.webhook_events = ["created"]  # only creates fire
    _endpoint(["*"])
    r = WebhookReceiver.objects.create(name="R", slug="r")
    r.name = "R2"
    r.save()  # update — should NOT fire
    assert list(WebhookDelivery.objects.values_list("event_type", flat=True)) == [
        "webhooks.webhookreceiver.created"
    ]
