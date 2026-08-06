"""Credential encryption, ADIF import, and eQSL upload tests."""
from __future__ import annotations

import datetime
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import fieldcrypto, logbook
from apps.cw.models import QSO, EQSLProfile, QRZProfile

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user(username="op", password="pw")


@pytest.fixture
def client_logged(client, user):
    client.force_login(user)
    return client


@pytest.fixture(autouse=True)
def isolated_key(tmp_path, settings):
    settings.CW_CREDENTIALS_KEY_FILE = str(tmp_path / "test.key")
    fieldcrypto.reset_cache()
    yield
    fieldcrypto.reset_cache()


class TestCredentialStorage:
    def test_password_encrypted_at_rest(self, user):
        profile = QRZProfile(user=user, username="n0call")
        profile.set_password("hunter2")
        profile.save()
        stored = QRZProfile.objects.get(user=user)
        assert stored.password.startswith("enc:")
        assert "hunter2" not in stored.password
        assert stored.get_password() == "hunter2"

    def test_key_file_created_restricted(self, user, settings):
        import os

        profile = QRZProfile(user=user)
        profile.set_password("x")
        mode = os.stat(settings.CW_CREDENTIALS_KEY_FILE).st_mode & 0o777
        assert mode == 0o600

    def test_legacy_plaintext_upgrades_on_read(self, user):
        QRZProfile.objects.create(user=user, username="n0call", password="oldplain")
        profile = QRZProfile.objects.get(user=user)
        assert profile.get_password() == "oldplain"
        profile.refresh_from_db()
        assert profile.password.startswith("enc:")  # transparently re-encrypted

    def test_lost_key_reads_as_unset_not_crash(self, user, settings, tmp_path):
        profile = QRZProfile(user=user)
        profile.set_password("secret")
        profile.save()
        settings.CW_CREDENTIALS_KEY_FILE = str(tmp_path / "different.key")
        fieldcrypto.reset_cache()
        assert QRZProfile.objects.get(user=user).get_password() == ""

    def test_api_never_echoes_password(self, client_logged):
        client_logged.post(
            reverse("cw-log-qrz"),
            json.dumps({"username": "n0call", "password": "hunter2"}),
            content_type="application/json",
        )
        for url in (reverse("cw-log-qrz"), reverse("cw-log-eqsl-config")):
            body = client_logged.get(url).content.decode()
            assert "hunter2" not in body
            assert "enc:" not in body  # not even the ciphertext

    def test_blank_password_post_preserves_existing(self, client_logged, user):
        client_logged.post(
            reverse("cw-log-qrz"),
            json.dumps({"username": "n0call", "password": "hunter2"}),
            content_type="application/json",
        )
        client_logged.post(
            reverse("cw-log-qrz"),
            json.dumps({"username": "renamed", "password": ""}),
            content_type="application/json",
        )
        profile = QRZProfile.objects.get(user=user)
        assert profile.username == "renamed"
        assert profile.get_password() == "hunter2"  # not clobbered


class TestADIFImport:
    SAMPLE = (
        "Some header text\n<adif_ver:5>3.1.4\n<EOH>\n"
        "<CALL:4>W1AW <QSO_DATE:8>20260101 <TIME_ON:6>123045 <BAND:3>20m "
        "<FREQ:7>14.0550 <MODE:2>CW <RST_SENT:3>599 <RST_RCVD:3>579 "
        "<NAME:5>Hiram <GRIDSQUARE:4>FN31 <EOR>\n"
        "<call:5>K1ABC <qso_date:8>20260102 <time_on:4>0800 <mode:5>PSK31 <eor>\n"
    )

    def test_parse_and_import(self, user):
        stats = logbook.import_adif(user, self.SAMPLE)
        assert stats == {"created": 2, "duplicates": 0, "errors": 0}
        w1aw = QSO.objects.get(user=user, call="W1AW")
        assert w1aw.when == datetime.datetime(2026, 1, 1, 12, 30, 45, tzinfo=datetime.timezone.utc)
        assert w1aw.band == "20m"
        assert w1aw.freq_mhz == 14.055
        assert w1aw.rst_rcvd == "579"
        assert w1aw.gridsquare == "FN31"
        assert w1aw.source == "import"
        k1abc = QSO.objects.get(user=user, call="K1ABC")
        assert k1abc.mode == "PSK31"
        assert k1abc.when.hour == 8

    def test_reimport_is_noop(self, user):
        logbook.import_adif(user, self.SAMPLE)
        stats = logbook.import_adif(user, self.SAMPLE)
        assert stats["created"] == 0
        assert stats["duplicates"] == 2
        assert QSO.objects.filter(user=user).count() == 2

    def test_export_import_roundtrip(self, user):
        when = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
        QSO.objects.create(
            user=user, call="JA1NUT", when=when, freq_mhz=7.03, band="40m",
            mode="CW", rst_sent="579", name="Shin", country="Japan",
        )
        adif = logbook.adif_export(QSO.objects.filter(user=user))
        QSO.objects.all().delete()
        stats = logbook.import_adif(user, adif)
        assert stats["created"] == 1
        back = QSO.objects.get(user=user, call="JA1NUT")
        assert back.when == when
        assert back.freq_mhz == 7.03
        assert back.country == "Japan"

    def test_malformed_records_counted_not_fatal(self, user):
        broken = "<EOH>\n<CALL:4>W1AW <EOR>\n<QSO_DATE:8>20260101 <EOR>\n"
        stats = logbook.import_adif(user, broken)
        assert stats["errors"] == 2  # no date on one, no call on the other
        assert stats["created"] == 0

    def test_upload_endpoint(self, client_logged, user):
        upload = io.BytesIO(self.SAMPLE.encode())
        upload.name = "old-log.adi"
        response = client_logged.post(reverse("cw-log-import"), {"adif": upload})
        payload = response.json().get("data") or response.json()
        assert payload["created"] == 2
        assert QSO.objects.filter(user=user).count() == 2

    def test_endpoint_requires_auth(self, client):
        assert client.post(reverse("cw-log-import")).status_code == 401


# ── eQSL against a fake server ────────────────────────────────────────────
class FakeEQSLHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        self.server.uploads.append(body)  # type: ignore[attr-defined]
        if "GOODPASS" in body:
            html = "<html>Result: 2 out of 2 records added</html>"
        else:
            html = "<html>Error: No such Username/Password found</html>"
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_eqsl(settings):
    server = HTTPServer(("127.0.0.1", 0), FakeEQSLHandler)
    server.uploads = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.EQSL_UPLOAD_URL = f"http://127.0.0.1:{server.server_address[1]}/qslcard/ImportADIF.cfm"
    yield server
    server.shutdown()
    server.server_close()


class TestEQSL:
    def _configure(self, user, password="GOODPASS"):
        profile = EQSLProfile(user=user, username="n0call")
        profile.set_password(password)
        profile.save()

    def test_upload_marks_qsos_sent(self, client_logged, user, fake_eqsl):
        self._configure(user)
        QSO.objects.create(user=user, call="W1AW")
        QSO.objects.create(user=user, call="K1ABC")
        response = client_logged.post(
            reverse("cw-log-eqsl"), "{}", content_type="application/json"
        )
        payload = response.json().get("data") or response.json()
        assert payload["uploaded"] == 2
        assert "2 out of 2" in payload["message"]
        assert QSO.objects.filter(user=user, eqsl_sent_at__isnull=False).count() == 2
        # the uploaded ADIF embedded credentials + records
        sent_body = fake_eqsl.uploads[0]
        assert "EQSL_USER" in sent_body and "W1AW" in sent_body

    def test_second_upload_sends_nothing_new(self, client_logged, user, fake_eqsl):
        self._configure(user)
        QSO.objects.create(user=user, call="W1AW")
        client_logged.post(reverse("cw-log-eqsl"), "{}", content_type="application/json")
        response = client_logged.post(
            reverse("cw-log-eqsl"), "{}", content_type="application/json"
        )
        payload = response.json().get("data") or response.json()
        assert payload["uploaded"] == 0

    def test_bad_credentials_surface_eqsl_error(self, client_logged, user, fake_eqsl):
        self._configure(user, password="WRONG")
        QSO.objects.create(user=user, call="W1AW")
        response = client_logged.post(
            reverse("cw-log-eqsl"), "{}", content_type="application/json"
        )
        assert response.status_code == 502
        assert QSO.objects.filter(user=user, eqsl_sent_at__isnull=False).count() == 0

    def test_unconfigured_gives_clear_error(self, client_logged, user):
        QSO.objects.create(user=user, call="W1AW")
        response = client_logged.post(
            reverse("cw-log-eqsl"), "{}", content_type="application/json"
        )
        assert response.status_code == 400


# ── QRZ logbook sync against a fake logbook.qrz.com ───────────────────────
class FakeQRZLogbookHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        import html as _html
        from urllib.parse import parse_qs

        length = int(self.headers.get("Content-Length", 0))
        params = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        server = self.server
        if params.get("KEY") != "GOODKEY":
            body = "RESULT=FAIL&REASON=invalid api key"
        elif params.get("ACTION") == "FETCH":
            adif = ("<call:4>W9ZZ <qso_date:8>20250101 <time_on:4>1200 "
                    "<band:3>40m <mode:2>CW <eor>")
            body = "RESULT=OK&COUNT=1&ADIF=" + _html.escape(adif)
        elif params.get("ACTION") == "INSERT":
            record = params.get("ADIF", "")
            server.inserted.append(record)  # type: ignore[attr-defined]
            if "DUP1AA" in record:
                body = "RESULT=FAIL&REASON=Unable to add QSO to database: duplicate"
            else:
                body = "RESULT=OK&COUNT=1&LOGID=42"
        else:
            body = "RESULT=FAIL&REASON=unknown action"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_qrz_logbook(settings):
    server = HTTPServer(("127.0.0.1", 0), FakeQRZLogbookHandler)
    server.inserted = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.QRZ_LOGBOOK_URL = f"http://127.0.0.1:{server.server_address[1]}/api"
    yield server
    server.shutdown()
    server.server_close()


class TestQRZLogbookSync:
    def _configure(self, user, key="GOODKEY"):
        profile = QRZProfile(user=user, username="op")
        profile.set_logbook_key(key)
        profile.save()

    def test_import_pulls_qrz_log(self, client_logged, user, fake_qrz_logbook):
        self._configure(user)
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "import"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["created"] == 1
        qso = QSO.objects.get(user=user, call="W9ZZ")
        assert qso.band == "40m"
        assert qso.source == "import"
        # repeat is a no-op
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "import"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["created"] == 0 and payload["duplicates"] == 1

    def test_export_pushes_unsent_and_marks(self, client_logged, user, fake_qrz_logbook):
        self._configure(user)
        QSO.objects.create(user=user, call="W1AW")
        QSO.objects.create(user=user, call="DUP1AA")  # fake server calls it a dup
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "export"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["exported"] == 1
        assert payload["duplicates"] == 1
        assert QSO.objects.filter(user=user, qrz_sent_at__isnull=True).count() == 0
        assert len(fake_qrz_logbook.inserted) == 2
        assert "<eor>" in fake_qrz_logbook.inserted[0].lower()
        # second export finds nothing new
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "export"}),
            content_type="application/json",
        )
        payload = response.json().get("data") or response.json()
        assert payload["exported"] == 0

    def test_bad_key_surfaces_error(self, client_logged, user, fake_qrz_logbook):
        self._configure(user, key="WRONG")
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "import"}),
            content_type="application/json",
        )
        assert response.status_code == 502

    def test_requires_key(self, client_logged, user):
        response = client_logged.post(
            reverse("cw-qrz-logbook"), json.dumps({"action": "import"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_unlink_clears_everything(self, client_logged, user):
        profile = QRZProfile(user=user, username="op")
        profile.set_password("x")
        profile.set_logbook_key("GOODKEY")
        profile.save()
        client_logged.post(
            reverse("cw-log-qrz"), json.dumps({"unlink": True}),
            content_type="application/json",
        )
        profile.refresh_from_db()
        assert profile.username == "" and profile.password == "" and profile.logbook_key == ""

    def test_callbook_page_renders(self, client_logged):
        content = client_logged.get(reverse("cw-callbook")).content.decode()
        assert 'id="cb-lookup"' in content
        assert 'id="cb-import"' in content
        assert 'id="cb-unlink"' in content
