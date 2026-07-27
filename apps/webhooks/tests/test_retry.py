"""run_due_deliveries claims due retries with an atomic conditional update."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.webhooks import services
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_real_enqueue():
    with mock.patch("apps.webhooks.services._enqueue_delivery") as m:
        yield m


def _retrying(next_attempt_at):
    ep = WebhookEndpoint.objects.create(name="e", target_url="https://hooks.example.com/x")
    return WebhookDelivery.objects.create(
        endpoint=ep,
        event_type="t.t.created",
        payload={},
        status=WebhookDelivery.Status.RETRYING,
        next_attempt_at=next_attempt_at,
    )


def test_due_retry_is_claimed(no_real_enqueue):
    d = _retrying(timezone.now() - timedelta(minutes=1))
    claimed = services.run_due_deliveries()
    assert claimed == 1
    d.refresh_from_db()
    assert d.status == WebhookDelivery.Status.PENDING
    assert d.next_attempt_at is None
    no_real_enqueue.assert_called_once_with(d.pk)


def test_future_retry_is_not_claimed():
    _retrying(timezone.now() + timedelta(minutes=5))
    assert services.run_due_deliveries() == 0


def test_non_retrying_is_ignored():
    ep = WebhookEndpoint.objects.create(name="e", target_url="https://hooks.example.com/x")
    WebhookDelivery.objects.create(
        endpoint=ep, event_type="t", payload={}, status=WebhookDelivery.Status.SUCCESS
    )
    assert services.run_due_deliveries() == 0
