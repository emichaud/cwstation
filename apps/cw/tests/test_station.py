"""Station callsign — the {mycall}/ADIF source, configurable per operator,
falling back to the username."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw.models import QSO, CWRig
from apps.cw.services import station_callsign

User = get_user_model()

pytestmark = pytest.mark.django_db


class TestStationCallsignHelper:
    def test_falls_back_to_username_uppercased(self):
        user = User.objects.create_user(username="n1krx", password="pw")
        assert station_callsign(user) == "N1KRX"

    def test_uses_configured_callsign_when_set(self):
        user = User.objects.create_user(username="operator7", password="pw")
        CWRig.objects.create(user=user, callsign="W1AW")
        assert station_callsign(user) == "W1AW"

    def test_blank_callsign_falls_back(self):
        user = User.objects.create_user(username="k5tr", password="pw")
        CWRig.objects.create(user=user, callsign="")
        assert station_callsign(user) == "K5TR"


class TestStationConfigEndpoint:
    @pytest.fixture
    def client_logged(self, client):
        user = User.objects.create_user(username="ab1cd", password="pw")
        client.force_login(user)
        return client, user

    def test_get_returns_username_fallback(self, client_logged):
        client, _ = client_logged
        data = client.get(reverse("cw-station-config")).json()
        payload = data.get("data") or data
        assert payload["callsign"] == ""          # nothing saved yet
        assert payload["resolved"] == "AB1CD"     # username fallback

    def test_post_saves_and_uppercases(self, client_logged):
        client, user = client_logged
        resp = client.post(
            reverse("cw-station-config"), json.dumps({"callsign": "w2xyz"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        payload = resp.json().get("data") or resp.json()
        assert payload["callsign"] == "W2XYZ"
        assert payload["resolved"] == "W2XYZ"
        assert CWRig.objects.get(user=user).callsign == "W2XYZ"

    def test_post_blank_clears_back_to_username(self, client_logged):
        client, _ = client_logged
        client.post(reverse("cw-station-config"), json.dumps({"callsign": "W2XYZ"}),
                    content_type="application/json")
        resp = client.post(reverse("cw-station-config"), json.dumps({"callsign": ""}),
                           content_type="application/json")
        payload = resp.json().get("data") or resp.json()
        assert payload["callsign"] == ""
        assert payload["resolved"] == "AB1CD"

    def test_rejects_overlong_callsign(self, client_logged):
        client, _ = client_logged
        resp = client.post(
            reverse("cw-station-config"), json.dumps({"callsign": "X" * 25}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_requires_auth(self, client):
        assert client.get(reverse("cw-station-config")).status_code == 401


class TestStationCallsignInAdif:
    def test_adif_export_uses_configured_callsign(self, client):
        user = User.objects.create_user(username="plain", password="pw")
        CWRig.objects.create(user=user, callsign="N1KRX")
        QSO.objects.create(user=user, call="W1AW")
        client.force_login(user)
        body = client.get(reverse("cw-log-adif")).content.decode()
        assert "N1KRX" in body            # the configured call, not "PLAIN"
        assert "station_callsign" in body.lower()


class TestStationVars:
    """Custom tags — user-defined {name} → value."""

    @pytest.fixture
    def client_logged(self, client):
        user = User.objects.create_user(username="op", password="pw")
        client.force_login(user)
        return client, user

    def _post(self, client, payload):
        return client.post(
            reverse("cw-station-vars"), json.dumps(payload),
            content_type="application/json",
        )

    def test_create_lists_and_normalizes(self, client_logged):
        client, user = client_logged
        # braces stripped, name lower-cased
        resp = self._post(client, {"name": "{Rig}", "value": "KW4420"})
        assert resp.status_code == 200
        payload = resp.json().get("data") or resp.json()
        assert payload["name"] == "rig"
        assert payload["value"] == "KW4420"

        listing = client.get(reverse("cw-station-vars")).json()
        rows = (listing.get("data") or listing)["vars"]
        assert [r["name"] for r in rows] == ["rig"]

    def test_reserved_names_rejected(self, client_logged):
        client, _ = client_logged
        for reserved in ("mycall", "call", "rst"):
            resp = self._post(client, {"name": reserved, "value": "x"})
            assert resp.status_code == 400

    def test_duplicate_rejected(self, client_logged):
        client, _ = client_logged
        self._post(client, {"name": "ant", "value": "dipole"})
        resp = self._post(client, {"name": "ant", "value": "beam"})
        assert resp.status_code == 409

    def test_update_and_delete(self, client_logged):
        from apps.cw.models import CWVariable
        client, user = client_logged
        body = self._post(client, {"name": "pwr", "value": "5W"}).json()
        created = body.get("data") or body
        vid = created["id"]
        self._post(client, {"id": vid, "value": "100W"})
        assert CWVariable.objects.get(pk=vid).value == "100W"
        self._post(client, {"id": vid, "delete": True})
        assert not CWVariable.objects.filter(pk=vid).exists()

    def test_value_required(self, client_logged):
        client, _ = client_logged
        assert self._post(client, {"name": "rig", "value": ""}).status_code == 400

    def test_requires_auth(self, client):
        assert client.get(reverse("cw-station-vars")).status_code == 401
