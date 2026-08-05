"""Rig-control tests against a fake rigctld — the wire protocol is exercised
end to end (client → TCP → server) with no Hamlib and no radio."""
from __future__ import annotations

import json
import socket
import socketserver
import threading

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import services, transmit
from apps.cw.models import CWRig
from apps.cw.rigctl import RigctldClient, RigError

User = get_user_model()


class FakeRigHandler(socketserver.StreamRequestHandler):
    """Speaks enough of the rigctld protocol for the CW station: f/F/m/M/t/T."""

    def handle(self) -> None:
        rig = self.server.rig  # type: ignore[attr-defined]
        for raw in self.rfile:
            parts = raw.decode().strip().split()
            if not parts:
                continue
            cmd, args = parts[0], parts[1:]
            rig["log"].append(raw.decode().strip())
            if cmd == "f":
                self.wfile.write(f"{rig['freq']}\n".encode())
            elif cmd == "F":
                rig["freq"] = int(float(args[0]))
                self.wfile.write(b"RPRT 0\n")
            elif cmd == "m":
                self.wfile.write(f"{rig['mode']}\n{rig['passband']}\n".encode())
            elif cmd == "M":
                rig["mode"] = args[0]
                rig["passband"] = int(args[1]) if len(args) > 1 else 0
                self.wfile.write(b"RPRT 0\n")
            elif cmd == "t":
                self.wfile.write(f"{1 if rig['ptt'] else 0}\n".encode())
            elif cmd == "T":
                rig["ptt"] = args[0] == "1"
                rig["log"].append(f"PTT->{rig['ptt']}")
                self.wfile.write(b"RPRT 0\n")
            else:
                self.wfile.write(b"RPRT -1\n")


@pytest.fixture
def fake_rig():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeRigHandler)
    server.daemon_threads = True
    server.rig = {"freq": 14055000, "mode": "CW", "passband": 500, "ptt": False, "log": []}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


class TestRigctldClient:
    def test_freq_mode_ptt_roundtrip(self, fake_rig):
        port = fake_rig.server_address[1]
        with RigctldClient("127.0.0.1", port) as client:
            assert client.get_freq() == 14055000
            client.set_freq(7030000)
            assert client.get_freq() == 7030000
            assert client.get_mode() == ("CW", 500)
            client.set_mode("USB", 2400)
            assert client.get_mode() == ("USB", 2400)
            assert client.get_ptt() is False
            client.set_ptt(True)
            assert client.get_ptt() is True
            client.set_ptt(False)

    def test_status_probe(self, fake_rig):
        port = fake_rig.server_address[1]
        with RigctldClient("127.0.0.1", port) as client:
            status = client.status()
        assert status == {"freq_hz": 14055000, "mode": "CW", "passband_hz": 500, "ptt": False}

    def test_unknown_command_raises(self, fake_rig):
        port = fake_rig.server_address[1]
        with RigctldClient("127.0.0.1", port) as client:
            with pytest.raises(RigError, match="RPRT -1"):
                client._request("Z", 1)

    def test_unreachable_raises(self):
        # grab a port and close it so nothing listens there
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        with pytest.raises(RigError, match="unreachable"):
            RigctldClient("127.0.0.1", dead_port, timeout=0.3).connect()


@pytest.mark.django_db
class TestTransmitSequence:
    def _session(self, user):
        return services.compose_send(user, "TEST DE OP", wpm=25, tone_hz=600)

    def test_ptt_wraps_playback(self, fake_rig):
        user = User.objects.create_user(username="op", password="pw")
        rig = CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1",
            port=fake_rig.server_address[1], ptt_lead_ms=0,
        )
        session = self._session(user)
        played: list[tuple[int, int]] = []

        def fake_player(audio, sample_rate, device):
            # PTT must be keyed while audio plays
            assert fake_rig.rig["ptt"] is True
            played.append((len(audio), sample_rate))

        state = transmit.transmit_session(rig, session, player=fake_player, blocking=True)
        assert played and played[0][1] == 8000
        assert fake_rig.rig["ptt"] is False  # unkeyed after
        assert state["error"] == ""
        # sequence: keyed exactly once, unkeyed exactly once, in order
        ptt_events = [e for e in fake_rig.rig["log"] if e.startswith("PTT->")]
        assert ptt_events == ["PTT->True", "PTT->False"]

    def test_ptt_released_even_when_playback_fails(self, fake_rig):
        user = User.objects.create_user(username="op2", password="pw")
        rig = CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1",
            port=fake_rig.server_address[1], ptt_lead_ms=0,
        )
        session = self._session(user)

        def broken_player(audio, sample_rate, device):
            raise RuntimeError("sound device exploded")

        state = transmit.transmit_session(rig, session, player=broken_player, blocking=True)
        assert "TX failed" in state["error"] or "exploded" in state["error"]
        assert fake_rig.rig["ptt"] is False  # the finally unkeyed it

    def test_refuses_when_ptt_already_keyed(self, fake_rig):
        user = User.objects.create_user(username="op3", password="pw")
        fake_rig.rig["ptt"] = True
        rig = CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1",
            port=fake_rig.server_address[1], ptt_lead_ms=0,
        )
        session = self._session(user)
        state = transmit.transmit_session(rig, session, player=lambda *a: None, blocking=True)
        assert "already keyed" in state["error"]
        assert fake_rig.rig["ptt"] is True  # untouched

    def test_vox_mode_skips_ptt(self, fake_rig):
        user = User.objects.create_user(username="op4", password="pw")
        rig = CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1",
            port=fake_rig.server_address[1], use_ptt=False,
        )
        session = self._session(user)
        transmit.transmit_session(rig, session, player=lambda *a: None, blocking=True)
        assert not [e for e in fake_rig.rig["log"] if e.startswith("PTT->")]


@pytest.mark.django_db
class TestRigEndpoints:
    @pytest.fixture
    def user(self):
        return User.objects.create_user(username="op9", password="pw")

    @pytest.fixture
    def client_logged(self, client, user):
        client.force_login(user)
        return client

    def _payload(self, response):
        data = response.json()
        return data.get("data") or data

    def test_get_creates_config_disconnected(self, client_logged):
        response = client_logged.get(reverse("cw-rig"))
        data = self._payload(response)
        assert data["enabled"] is False
        assert data["connected"] is False

    def test_probe_reads_rig_state(self, client_logged, user, fake_rig):
        CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1", port=fake_rig.server_address[1]
        )
        data = self._payload(client_logged.get(reverse("cw-rig")))
        assert data["connected"] is True
        assert data["freq_hz"] == 14055000
        assert data["mode"] == "CW"
        assert data["ptt"] is False

    def test_post_sets_frequency_and_mode(self, client_logged, user, fake_rig):
        CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1", port=fake_rig.server_address[1]
        )
        response = client_logged.post(
            reverse("cw-rig"),
            json.dumps({"freq_hz": 7030000, "mode": "CW"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert fake_rig.rig["freq"] == 7030000

    def test_bad_mode_rejected(self, client_logged, user, fake_rig):
        CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1", port=fake_rig.server_address[1]
        )
        response = client_logged.post(
            reverse("cw-rig"), json.dumps({"mode": "CHAOS"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_tx_endpoint_keys_session(self, client_logged, user, fake_rig, monkeypatch):
        played = []
        monkeypatch.setattr(
            "apps.cw.transmit._default_player",
            lambda audio, fs, device: played.append(len(audio)),
        )
        CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1",
            port=fake_rig.server_address[1], ptt_lead_ms=0,
        )
        session = services.compose_send(user, "73", wpm=25, tone_hz=600)
        response = client_logged.post(
            reverse("cw-rig-tx"),
            json.dumps({"session_id": session.pk}),
            content_type="application/json",
        )
        assert response.status_code == 200
        # the TX thread is async — wait for it to finish
        import time

        for _ in range(50):
            if not transmit.tx_state()["transmitting"] and played:
                break
            time.sleep(0.05)
        assert played
        assert fake_rig.rig["ptt"] is False

    def test_tx_requires_enabled_rig(self, client_logged, user):
        session = services.compose_send(user, "73", wpm=25, tone_hz=600)
        response = client_logged.post(
            reverse("cw-rig-tx"),
            json.dumps({"session_id": session.pk}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_tx_scoped_to_own_sessions(self, client_logged, user, fake_rig):
        other = User.objects.create_user(username="other", password="pw")
        their_session = services.compose_send(other, "73", wpm=25, tone_hz=600)
        CWRig.objects.create(
            user=user, enabled=True, host="127.0.0.1", port=fake_rig.server_address[1]
        )
        response = client_logged.post(
            reverse("cw-rig-tx"),
            json.dumps({"session_id": their_session.pk}),
            content_type="application/json",
        )
        assert response.status_code == 404
