"""Rig Setup launcher tests — daemon lifecycle, catalog parsing, validation.

Lifecycle tests run the REAL rigctld dummy rig when Hamlib is installed
(it is in CI-less local dev; they skip cleanly where it isn't)."""
from __future__ import annotations

import json
import shutil

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import rigdaemon
from apps.cw.models import CWRig
from apps.cw.rigctl import RigError

User = get_user_model()

has_hamlib = shutil.which("rigctld") is not None
needs_hamlib = pytest.mark.skipif(not has_hamlib, reason="hamlib not installed")

TEST_TCP_PORT = 45320  # away from any real rigctld on 4532


@pytest.fixture(autouse=True)
def daemon_cleanup():
    yield
    rigdaemon.stop()


class TestValidation:
    def test_rejects_bad_serial_path(self):
        with pytest.raises(RigError, match="Not a serial device"):
            rigdaemon.start(3085, serial_port="/dev/ttyUSB0; rm -rf /", tcp_port=TEST_TCP_PORT)

    def test_rejects_missing_device(self):
        with pytest.raises(RigError, match="doesn't exist"):
            rigdaemon.start(3085, serial_port="/dev/cu.nonexistent-xyz", tcp_port=TEST_TCP_PORT)

    def test_rejects_bad_baud(self):
        with pytest.raises(RigError, match="baud"):
            rigdaemon.start(1, baud=1337, tcp_port=TEST_TCP_PORT)


class TestCatalogAndPorts:
    @needs_hamlib
    def test_model_catalog_parses(self):
        models = rigdaemon.list_models(refresh=True)
        assert len(models) > 100  # hamlib ships hundreds
        dummy = next(m for m in models if m["id"] == 1)
        assert "dummy" in dummy["model"].lower() or "hamlib" in dummy["mfg"].lower()
        assert all(isinstance(m["id"], int) for m in models[:20])

    def test_serial_ports_shape(self):
        ports = rigdaemon.list_serial_ports()
        for p in ports:  # may be empty on CI boxes
            assert p["device"].startswith("/dev/")
            assert "Bluetooth" not in p["device"]

    @needs_hamlib
    def test_hamlib_status(self):
        status = rigdaemon.hamlib_status()
        assert status["installed"] is True
        assert "Hamlib" in status["version"]


@needs_hamlib
class TestDaemonLifecycle:
    def test_start_probe_stop_dummy_rig(self):
        state = rigdaemon.start(1, tcp_port=TEST_TCP_PORT)
        assert state["running"] is True
        assert state["reachable"] is True
        assert state["freq_hz"] > 0  # real CAT probe through real hamlib
        assert state["spec"]["model"] == 1
        assert any("rigctld" in line for line in state["log"])

        with pytest.raises(RigError, match="already running"):
            rigdaemon.start(1, tcp_port=TEST_TCP_PORT)

        stopped = rigdaemon.stop()
        assert stopped["running"] is False

    def test_port_conflict_detected(self):
        rigdaemon.start(1, tcp_port=TEST_TCP_PORT)
        # module state thinks nothing is running after a manual clear, but the
        # socket is still bound — the port check must catch it
        proc = rigdaemon._state["proc"]
        rigdaemon._state["proc"] = None
        try:
            with pytest.raises(RigError, match="already listening"):
                rigdaemon.start(1, tcp_port=TEST_TCP_PORT)
        finally:
            rigdaemon._state["proc"] = proc


@pytest.mark.django_db
class TestSetupEndpoints:
    @pytest.fixture
    def client_logged(self, client):
        user = User.objects.create_user(username="op", password="pw")
        client.force_login(user)
        return client

    def test_requires_auth(self, client):
        assert client.get(reverse("cw-rig-setup-data")).status_code == 401
        assert client.post(reverse("cw-rig-daemon")).status_code == 401

    def test_setup_page_renders(self, client_logged):
        content = client_logged.get(reverse("cw-rig-setup")).content.decode()
        assert 'id="rs-models"' in content
        assert 'id="rs-dummy"' in content
        assert 'id="rs-log"' in content

    @needs_hamlib
    def test_setup_data_payload(self, client_logged):
        data = client_logged.get(reverse("cw-rig-setup-data")).json()
        payload = data.get("data") or data
        assert payload["hamlib"]["installed"] is True
        assert len(payload["models"]) > 100
        assert payload["daemon"]["running"] is False

    def test_setup_data_lists_custom_images(self, client_logged, tmp_path, settings):
        # no folder / no images -> empty dict; a numbered file -> mapped to a URL
        import os
        settings.BASE_DIR = tmp_path
        os.makedirs(tmp_path / "static" / "cw" / "rigs")
        (tmp_path / "static" / "cw" / "rigs" / "3085.png").write_bytes(b"x")
        (tmp_path / "static" / "cw" / "rigs" / "notes.txt").write_text("ignore me")
        data = client_logged.get(reverse("cw-rig-setup-data")).json()
        payload = data.get("data") or data
        assert "3085" in payload["custom_images"]
        assert payload["custom_images"]["3085"].endswith("cw/rigs/3085.png")
        assert "notes" not in payload["custom_images"]

    def test_daemon_start_requires_model(self, client_logged):
        response = client_logged.post(
            reverse("cw-rig-daemon"), json.dumps({"action": "start"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @needs_hamlib
    def test_start_saves_config_and_arms_rig(self, client_logged, monkeypatch):
        # steer the endpoint's daemon to the test port so it can't collide
        original = rigdaemon.start
        monkeypatch.setattr(
            rigdaemon, "start",
            lambda model, serial_port=None, baud=None: original(
                model, serial_port=serial_port, baud=baud, tcp_port=TEST_TCP_PORT
            ),
        )
        response = client_logged.post(
            reverse("cw-rig-daemon"),
            json.dumps({"action": "start", "model": 1}),
            content_type="application/json",
        )
        assert response.status_code == 200
        payload = response.json().get("data") or response.json()
        assert payload["daemon"]["reachable"] is True

        config = CWRig.objects.get(user__username="op")
        assert config.enabled is True
        assert config.rig_model == 1
        assert config.port == TEST_TCP_PORT  # rig panel now points at the daemon
