"""Message-key (macro) tests: seeding, CRUD endpoint, and Send page wiring."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw.models import DEFAULT_MACROS, CWMacro

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def user() -> object:
    return User.objects.create_user(username="op", password="pw")


@pytest.fixture
def client_logged(client, user):
    client.force_login(user)
    return client


def macros_url() -> str:
    return reverse("cw-macros")


def get_macros(client) -> list[dict]:
    payload = client.get(macros_url()).json()
    return (payload.get("data") or payload)["macros"]


class TestSeedingAndList:
    def test_first_get_seeds_defaults(self, client_logged, user):
        rows = get_macros(client_logged)
        assert [m["name"] for m in rows] == [name for name, _ in DEFAULT_MACROS]
        assert CWMacro.objects.filter(user=user).count() == len(DEFAULT_MACROS)

    def test_seeding_is_idempotent(self, client_logged, user):
        get_macros(client_logged)
        CWMacro.objects.filter(user=user, name="cq").delete()
        rows = get_macros(client_logged)  # must NOT re-seed over edits
        assert "cq" not in [m["name"] for m in rows]

    def test_requires_auth(self, client):
        assert client.get(macros_url()).status_code == 401


class TestCreateUpdateDelete:
    def test_create(self, client_logged, user):
        response = client_logged.post(
            macros_url(),
            json.dumps({"name": "/Test", "text": "TEST DE {mycall} K"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        macro = CWMacro.objects.get(user=user, name="test")  # normalized
        assert macro.text == "TEST DE {mycall} K"

    def test_update(self, client_logged, user):
        macro = CWMacro.objects.create(user=user, name="cq", text="OLD")
        client_logged.post(
            macros_url(),
            json.dumps({"id": macro.pk, "text": "CQ CQ DE {mycall} K"}),
            content_type="application/json",
        )
        macro.refresh_from_db()
        assert macro.text == "CQ CQ DE {mycall} K"

    def test_delete(self, client_logged, user):
        macro = CWMacro.objects.create(user=user, name="cq", text="X")
        client_logged.post(
            macros_url(),
            json.dumps({"id": macro.pk, "delete": True}),
            content_type="application/json",
        )
        assert not CWMacro.objects.filter(pk=macro.pk).exists()

    def test_duplicate_name_rejected(self, client_logged, user):
        CWMacro.objects.create(user=user, name="cq", text="X")
        response = client_logged.post(
            macros_url(),
            json.dumps({"name": "cq", "text": "Y"}),
            content_type="application/json",
        )
        assert response.status_code == 409

    def test_bad_name_rejected(self, client_logged):
        response = client_logged.post(
            macros_url(),
            json.dumps({"name": "has spaces!", "text": "X"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_cannot_touch_other_users_macro(self, client_logged):
        other = User.objects.create_user(username="other", password="pw")
        theirs = CWMacro.objects.create(user=other, name="cq", text="THEIRS")
        response = client_logged.post(
            macros_url(),
            json.dumps({"id": theirs.pk, "delete": True}),
            content_type="application/json",
        )
        assert response.status_code == 404
        assert CWMacro.objects.filter(pk=theirs.pk).exists()


class TestSendSetupPage:
    """/send is now the setup page: macro bank + tag bank + callsign, no composer."""

    def test_setup_page_has_banks_and_callsign(self, client_logged):
        content = client_logged.get(reverse("cw-send")).content.decode()
        assert 'id="cw-bank"' in content        # message-key editor
        assert 'id="cw-vars"' in content        # custom-tag editor
        assert 'id="cw-mycall-input"' in content  # callsign editor
        assert 'id="def-wpm"' in content        # default speed
        # it is a setup page — no live composer
        assert 'id="cw-keycaps"' not in content

    def test_decode_keyer_wiring(self, client_logged):
        """The full keyer (composer + inserts + defaults) lives on /decode."""
        content = client_logged.get(reverse("cw-decode")).content.decode()
        assert 'id="cw-dec-text"' in content     # composer
        assert 'id="cw-dec-keycaps"' in content  # macro chips
        assert 'id="cw-dec-vars"' in content     # tag chips
        assert 'mycall: "OP"' in content

    def test_reply_context_feeds_the_keyer(self, client_logged):
        content = client_logged.get(reverse("cw-decode") + "?to=W1AW").content.decode()
        assert 'call: "W1AW"' in content
        assert "W1AW DE OP OP K" in content       # standard reply prefilled
