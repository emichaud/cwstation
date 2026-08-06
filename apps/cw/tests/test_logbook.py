"""Logbook tests — band mapping, smart quick-log, ADIF, QRZ, scoping."""
from __future__ import annotations

import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import logbook, services
from apps.cw.models import QSO, QRZProfile

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user(username="op", password="pw")


@pytest.fixture
def client_logged(client, user):
    client.force_login(user)
    return client


class TestBandMapping:
    @pytest.mark.parametrize("mhz,band", [
        (7.030, "40m"), (14.055, "20m"), (3.560, "80m"), (28.5, "10m"),
        (144.2, "2m"), (10.12, "30m"), (None, ""), (2.5, ""),
    ])
    def test_band_for_freq(self, mhz, band):
        assert logbook.band_for_freq(mhz) == band


class TestQuickLog:
    def test_links_session_and_inherits_mode_and_time(self, user):
        session = services.compose_send(user, "W1AW DE OP K", wpm=20, tone_hz=600)
        qso = logbook.quick_log(user, "w1aw", session=session, freq_hz=14055000)
        assert qso.call == "W1AW"
        assert qso.session_id == session.pk
        assert qso.when == session.created_at
        assert qso.mode == "CW"
        assert qso.freq_mhz == 14.055
        assert qso.band == "20m"

    def test_fldigi_session_logs_psk31(self, user):
        session = services.compose_send(user, "TEST", wpm=20, tone_hz=600)
        session.telemetry["meta"]["engine"] = "fldigi:BPSK31"
        session.save()
        qso = logbook.quick_log(user, "K1ABC", session=session)
        assert qso.mode == "PSK31"

    def test_worked_before_prefills_details(self, user):
        QSO.objects.create(
            user=user, call="W1AW", name="Hiram Maxim", qth="Newington, CT",
            gridsquare="FN31", country="United States",
        )
        qso = logbook.quick_log(user, "W1AW")
        assert qso.name == "Hiram Maxim"
        assert qso.qth == "Newington, CT"
        assert qso.gridsquare == "FN31"

    def test_endpoint_validates_and_scopes(self, client_logged, user):
        other = User.objects.create_user(username="other", password="pw")
        theirs = services.compose_send(other, "X", wpm=20, tone_hz=600)
        response = client_logged.post(
            reverse("cw-log-quick"),
            json.dumps({"call": "W1AW", "session_id": theirs.pk}),
            content_type="application/json",
        )
        assert response.status_code == 200
        qso = QSO.objects.get(user=user)
        assert qso.session_id is None  # someone else's session doesn't link

        bad = client_logged.post(
            reverse("cw-log-quick"), json.dumps({"call": "not a call!"}),
            content_type="application/json",
        )
        assert bad.status_code == 400

    def test_endpoint_reports_worked_before(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW")
        response = client_logged.post(
            reverse("cw-log-quick"), json.dumps({"call": "W1AW"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["worked_before"] == 1


class TestADIF:
    def test_export_format(self, user):
        when = datetime.datetime(2026, 8, 6, 14, 30, 15, tzinfo=datetime.timezone.utc)
        QSO.objects.create(
            user=user, call="W1AW", when=when, freq_mhz=14.055, band="20m",
            mode="CW", rst_sent="599", rst_rcvd="579", name="Hiram",
            qth="Newington", gridsquare="FN31", country="United States",
            comment="first  contact\nvia the tape",
        )
        adif = logbook.adif_export(QSO.objects.filter(user=user), station_call="OP")
        assert "<adif_ver:5>3.1.4" in adif
        assert "<EOH>" in adif
        assert "<call:4>W1AW" in adif
        assert "<qso_date:8>20260806" in adif
        assert "<time_on:6>143015" in adif
        assert "<band:3>20m" in adif
        assert "<freq:7>14.0550" in adif
        assert "<mode:2>CW" in adif
        assert "<rst_sent:3>599" in adif
        assert "<rst_rcvd:3>579" in adif
        assert "<gridsquare:4>FN31" in adif
        assert "<station_callsign:2>OP" in adif
        assert "<comment:26>first contact via the tape" in adif  # whitespace collapsed
        assert adif.strip().endswith("<EOR>")

    def test_times_export_in_utc(self, user):
        est = datetime.timezone(datetime.timedelta(hours=-5))
        QSO.objects.create(
            user=user, call="W1AW",
            when=datetime.datetime(2026, 1, 2, 23, 30, 0, tzinfo=est),
        )
        adif = logbook.adif_export(QSO.objects.filter(user=user))
        assert "<qso_date:8>20260103" in adif  # rolls over the UTC midnight
        assert "<time_on:6>043000" in adif

    def test_download_endpoint_filters(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW", band="20m")
        QSO.objects.create(user=user, call="K5TR", band="40m")
        other = User.objects.create_user(username="other2", password="pw")
        QSO.objects.create(user=other, call="SPY1XX")

        response = client_logged.get(reverse("cw-log-adif") + "?band=20m")
        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        body = response.content.decode()
        assert "W1AW" in body
        assert "K5TR" not in body  # band filter applied
        assert "SPY1XX" not in body  # other users never leak

    def test_requires_auth(self, client):
        assert client.get(reverse("cw-log-adif")).status_code == 401


# ── QRZ against a fake XML server ─────────────────────────────────────────
class FakeQRZHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        server = self.server
        if "username" in params:
            if params.get("password") == "goodpass":
                body = "<QRZDatabase><Session><Key>sess-key-1</Key></Session></QRZDatabase>"
            else:
                body = "<QRZDatabase><Session><Error>Username/password incorrect</Error></Session></QRZDatabase>"
        elif params.get("s") in server.valid_keys:  # type: ignore[attr-defined]
            call = params.get("callsign", "").upper()
            if call == "W1AW":
                body = ("<QRZDatabase><Callsign><call>W1AW</call><fname>Hiram</fname>"
                        "<name>Maxim</name><addr2>Newington</addr2><state>CT</state>"
                        "<grid>FN31pr</grid><country>United States</country></Callsign>"
                        "<Session><Key>sess-key-1</Key></Session></QRZDatabase>")
            else:
                body = f"<QRZDatabase><Session><Error>Not found: {call}</Error></Session></QRZDatabase>"
        else:
            body = "<QRZDatabase><Session><Error>Session Timeout</Error></Session></QRZDatabase>"
        data = ('<?xml version="1.0"?>' + body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_qrz(settings):
    server = HTTPServer(("127.0.0.1", 0), FakeQRZHandler)
    server.valid_keys = {"sess-key-1"}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.QRZ_XML_URL = f"http://127.0.0.1:{server.server_address[1]}/xml/"
    yield server
    server.shutdown()
    server.server_close()


class TestQRZ:
    def test_lookup_enriches_quick_log(self, user, fake_qrz):
        QRZProfile.objects.create(user=user, username="op", password="goodpass")
        qso = logbook.quick_log(user, "W1AW")
        assert qso.name == "Hiram Maxim"
        assert qso.qth == "Newington, CT"
        assert qso.gridsquare == "FN31pr"
        assert qso.country == "United States"

    def test_stale_session_reauths_once(self, user, fake_qrz):
        QRZProfile.objects.create(
            user=user, username="op", password="goodpass", session_key="stale-key"
        )
        qso = logbook.quick_log(user, "W1AW")
        assert qso.name == "Hiram Maxim"
        profile = QRZProfile.objects.get(user=user)
        assert profile.session_key == "sess-key-1"

    def test_bad_credentials_degrade_gracefully(self, user, fake_qrz):
        QRZProfile.objects.create(user=user, username="op", password="wrong")
        qso = logbook.quick_log(user, "W1AW")  # must not raise
        assert qso.name == ""

    def test_no_profile_no_lookup(self, user):
        qso = logbook.quick_log(user, "W1AW")
        assert qso.name == ""

    def test_config_endpoint_tests_credentials(self, client_logged, fake_qrz):
        response = client_logged.post(
            reverse("cw-log-qrz"),
            json.dumps({"username": "op", "password": "goodpass", "test_call": "W1AW"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["test"]["name"] == "Hiram Maxim"


class TestSearchIntegration:
    def test_logbook_and_sessions_registered_for_authenticated_users(self, client_logged):
        response = client_logged.get("/search/")
        labels = {s["model_label"] for s in response.context["indexed_sources"] if s["kind"] == "model"}
        assert "cw.QSO" in labels
        assert "cw.CWSession" in labels

    def test_search_finds_own_qso_only(self, client_logged, user):
        from apps.search.registry import search_all

        QSO.objects.create(user=user, call="W1AW", name="Hiram Maxim")
        other = User.objects.create_user(username="other9", password="pw")
        QSO.objects.create(user=other, call="W1AW", name="Someone Else")
        hits = [h for h in search_all("W1AW", user=user) if h.model_label == "cw.QSO"]
        assert len(hits) == 1


class TestQSOFormAndLookup:
    def test_form_pages_render_custom_template(self, client_logged, user):
        content = client_logged.get(reverse("cw/log-create")).content.decode()
        assert 'id="qso-form"' in content
        assert "cw-call-input" in content
        assert "cw-mode-chip" in content
        qso = QSO.objects.create(user=user, call="W1AW")
        content = client_logged.get(reverse("cw/log-update", args=[qso.pk])).content.decode()
        assert "QSO · W1AW" in content

    def test_create_parses_utc_time(self, client_logged, user):
        response = client_logged.post(reverse("cw/log-create"), {
            "call": "k5tr", "when": "2026-08-06T14:30", "freq_mhz": "14.055",
            "mode": "CW", "rst_sent": "599", "rst_rcvd": "579",
            "name": "", "qth": "", "gridsquare": "", "country": "", "comment": "",
        })
        assert response.status_code in (302, 200), response.status_code
        qso = QSO.objects.get(user=user)
        assert qso.call == "K5TR"  # uppercased by the form
        assert qso.when.astimezone(datetime.timezone.utc).hour == 14  # entered AS UTC
        assert qso.band == "20m"  # derived on save

    def test_lookup_endpoint_history_shape(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW", name="Hiram", qth="Newington")
        payload = client_logged.get(reverse("cw-log-lookup") + "?call=W1AW").json()
        d = payload.get("data") or payload
        assert d["worked"] == 1
        assert d["last"]["name"] == "Hiram"

    def test_lookup_rejects_junk(self, client_logged):
        assert client_logged.get(reverse("cw-log-lookup") + "?call=nope!").status_code == 400

    def test_lookup_requires_auth(self, client):
        assert client.get(reverse("cw-log-lookup") + "?call=W1AW").status_code == 401


class TestLogbookPage:
    def test_requires_login(self, client):
        assert client.get(reverse("cw/log-list")).status_code == 302

    def test_renders_with_filters_and_export(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW", band="20m", mode="CW")
        QSO.objects.create(user=user, call="K1ABC", band="40m", mode="PSK31")
        content = client_logged.get(reverse("cw/log-list")).content.decode()
        assert "↓ ADIF" in content
        assert "?band=20m" in content and "?mode=PSK31" in content
        assert "qrz↗" in content

    def test_filter_chips_are_deduplicated(self, client_logged, user):
        # Meta.ordering rides into DISTINCT unless cleared — regression guard
        for _ in range(3):
            QSO.objects.create(user=user, call="W1AW", band="20m", mode="CW")
        response = client_logged.get(reverse("cw/log-list"))
        assert response.context["log_bands"] == ["20m"]
        assert response.context["log_modes"] == ["CW"]

    def test_band_filter_narrows_list(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW", band="20m")
        QSO.objects.create(user=user, call="K5TR", band="40m")
        response = client_logged.get(reverse("cw/log-list") + "?band=40m")
        ids = [o.call for o in response.context["object_list"]]
        assert ids == ["K5TR"]

    def test_list_scoped_to_owner(self, client_logged, user):
        other = User.objects.create_user(username="other3", password="pw")
        QSO.objects.create(user=other, call="SPY1XX")
        response = client_logged.get(reverse("cw/log-list"))
        assert "SPY1XX" not in response.content.decode()
