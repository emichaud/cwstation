"""Secret lifecycle across surfaces (acceptance round 1, F-004/F-005).

- The detail pages must actually render the documented actions (test / reveal /
  rotate / replay) — round 1 found the views existed but no template exposed them.
- ``secret`` is write-only: settable on create/update from every scripted
  surface, never serialized back out, readable only via the staff POST reveal.
- Receivers get reveal/rotate parity with endpoints.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.smallstack.models import APIToken
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint, WebhookReceiver

pytestmark = pytest.mark.django_db

ENDPOINTS_URL = "/smallstack/api/webhooks/endpoints/"
RECEIVERS_URL = "/smallstack/api/webhooks/receivers/"


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(
        username="wh_secret_staff", password="x", is_staff=True
    )


@pytest.fixture
def staff_client(client, staff):
    client.force_login(staff)
    return client


@pytest.fixture
def auth_header(staff):
    _, raw_key = APIToken.create_token(staff, name="wh-secret")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def endpoint(db):
    return WebhookEndpoint.objects.create(
        name="Catcher", target_url="https://hooks.example.com/c", event_filter=["*"]
    )


@pytest.fixture
def receiver(db):
    return WebhookReceiver.objects.create(name="Stripe", slug="stripe-secrets")


# ---------------------------------------------------------------------------
# F-004 — the documented actions render on the detail pages
# ---------------------------------------------------------------------------


def test_endpoint_detail_renders_action_buttons(staff_client, endpoint):
    resp = staff_client.get(f"/smallstack/webhooks/endpoints/{endpoint.pk}/")
    html = resp.content.decode()
    assert f"/smallstack/webhooks/endpoints/{endpoint.pk}/test/" in html
    assert f"/smallstack/webhooks/endpoints/{endpoint.pk}/reveal/" in html
    assert f"/smallstack/webhooks/endpoints/{endpoint.pk}/rotate/" in html


def test_delivery_detail_renders_replay_button(staff_client, endpoint):
    d = WebhookDelivery.objects.create(
        endpoint=endpoint, event_type="t.t.created", payload={}, status="dead"
    )
    resp = staff_client.get(f"/smallstack/webhooks/deliveries/{d.pk}/")
    assert f"/smallstack/webhooks/deliveries/{d.pk}/replay/" in resp.content.decode()


def test_receiver_detail_renders_reveal_and_rotate(staff_client, receiver):
    resp = staff_client.get(f"/smallstack/webhooks/receivers/{receiver.pk}/")
    html = resp.content.decode()
    assert f"/smallstack/webhooks/receivers/{receiver.pk}/reveal/" in html
    assert f"/smallstack/webhooks/receivers/{receiver.pk}/rotate/" in html


# ---------------------------------------------------------------------------
# F-005 — receiver reveal/rotate parity
# ---------------------------------------------------------------------------


def test_receiver_reveal_staff_post_only(staff_client, client, receiver):
    url = f"/smallstack/webhooks/receivers/{receiver.pk}/reveal/"
    assert staff_client.post(url).json()["secret"] == receiver.secret
    assert staff_client.get(url).status_code == 405  # POST-only
    client.logout()
    assert client.post(url).status_code == 403  # anonymous


def test_receiver_rotate_changes_secret(staff_client, receiver):
    old = receiver.secret
    resp = staff_client.post(f"/smallstack/webhooks/receivers/{receiver.pk}/rotate/")
    assert resp.status_code == 302
    receiver.refresh_from_db()
    assert receiver.secret and receiver.secret != old


# ---------------------------------------------------------------------------
# F-005 — write-only secret on create/update, never serialized out
# ---------------------------------------------------------------------------


def test_sc_new_webhook_accepts_secret(staff):
    call_command(
        "sc", "new", "webhook",
        "--name", "KnownSecret", "--target_url", "https://hooks.example.com/k",
        "--secret", "test-secret",
        "--user", staff.username, "--json", stdout=StringIO(),
    )
    assert WebhookEndpoint.objects.get(name="KnownSecret").secret == "test-secret"


def test_sc_new_receiver_accepts_provider_secret(staff):
    call_command(
        "sc", "new", "webhookreceiver",
        "--name", "Stripe live", "--slug", "stripe-live",
        "--secret", "whsec_abc123",
        "--user", staff.username, "--json", stdout=StringIO(),
    )
    assert WebhookReceiver.objects.get(slug="stripe-live").secret == "whsec_abc123"


def test_rest_secret_write_only(client, auth_header, endpoint):
    # settable on create…
    resp = client.post(
        ENDPOINTS_URL,
        data=json.dumps({
            "name": "RestSecret",
            "target_url": "https://hooks.example.com/rs",
            "secret": "rest-secret",
        }),
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 201, resp.content
    assert WebhookEndpoint.objects.get(name="RestSecret").secret == "rest-secret"
    # …but never serialized back out
    assert "secret" not in resp.json()
    detail = client.get(f"{ENDPOINTS_URL}{endpoint.pk}/", **auth_header).json()
    assert "secret" not in detail


def test_rest_patch_blank_secret_keeps_current(client, auth_header, endpoint):
    original = endpoint.secret
    resp = client.patch(
        f"{ENDPOINTS_URL}{endpoint.pk}/",
        data=json.dumps({"name": "Renamed"}),
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200, resp.content
    endpoint.refresh_from_db()
    assert endpoint.name == "Renamed"
    assert endpoint.secret == original  # omitted secret ⇒ unchanged


def test_create_without_secret_still_autogenerates(client, auth_header):
    resp = client.post(
        ENDPOINTS_URL,
        data=json.dumps({"name": "AutoGen", "target_url": "https://hooks.example.com/ag"}),
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 201, resp.content
    assert WebhookEndpoint.objects.get(name="AutoGen").secret
