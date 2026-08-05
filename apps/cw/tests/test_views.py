"""View + model tests for the CW app. Engine DSP is covered in test_engine.py;
these prove the web layer: auth, decode/send flows, per-user scoping, audio."""
from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.cw.engine import synthesize_cw
from apps.cw.engine.wav import wav_bytes_from_float32
from apps.cw.models import CWSession

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def user() -> object:
    return User.objects.create_user(
        username="op", email="op@example.com", password="testpass123"
    )


@pytest.fixture
def other_user() -> object:
    return User.objects.create_user(
        username="other", email="other@example.com", password="testpass123"
    )


@pytest.fixture
def client_logged(client: Client, user: object) -> Client:
    client.force_login(user)
    return client


class TestAuth:
    @pytest.mark.parametrize("name", ["cw-monitor", "cw-decode", "cw-send", "cw/sessions-list"])
    def test_pages_require_login(self, client: Client, name: str) -> None:
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "login" in response["Location"]

    @pytest.mark.parametrize("name", ["cw-monitor", "cw-decode", "cw-send", "cw/sessions-list"])
    def test_pages_render_logged_in(self, client_logged: Client, name: str) -> None:
        response = client_logged.get(reverse(name))
        assert response.status_code == 200


class TestPracticeDecode:
    def test_creates_session_with_replay_telemetry(self, client_logged: Client, user: object) -> None:
        response = client_logged.post(reverse("cw-decode"), {
            "mode": "practice", "text": "CQ DE W1AW", "wpm": 20, "tone_hz": 600,
        })
        assert response.status_code == 302
        session = CWSession.objects.get(user=user)
        assert session.direction == CWSession.Direction.RECEIVED
        assert session.source == CWSession.Source.SYNTH
        assert session.text == "CQ DE W1AW"
        assert session.truth == "CQ DE W1AW"
        assert "W1AW" in session.callsigns
        assert session.can_replay
        assert session.accuracy == 1.0

    def test_invalid_form_rerenders(self, client_logged: Client) -> None:
        response = client_logged.post(reverse("cw-decode"), {
            "mode": "practice", "text": "", "wpm": 20, "tone_hz": 600,
        })
        assert response.status_code == 200
        assert CWSession.objects.count() == 0


class TestWavDecode:
    def test_decodes_uploaded_wav(self, client_logged: Client, user: object) -> None:
        r = synthesize_cw("TEST DE AB1CD", wpm=20, tone_hz=600, sample_rate=8000)
        blob = wav_bytes_from_float32(r.audio, r.sample_rate)
        upload = io.BytesIO(blob)
        upload.name = "signal.wav"
        response = client_logged.post(reverse("cw-decode"), {
            "mode": "wav", "wav": upload, "tone_hz": 600,
        })
        assert response.status_code == 302
        session = CWSession.objects.get(user=user)
        assert session.source == CWSession.Source.WAV
        assert session.text == "TEST DE AB1CD"
        assert not session.has_audio  # uploads are not stored

    def test_garbage_file_shows_error(self, client_logged: Client) -> None:
        upload = io.BytesIO(b"not a wav file at all")
        upload.name = "junk.wav"
        response = client_logged.post(reverse("cw-decode"), {
            "mode": "wav", "wav": upload, "tone_hz": 600,
        })
        assert response.status_code == 200
        assert CWSession.objects.count() == 0


class TestSend:
    def test_composes_and_self_decodes(self, client_logged: Client, user: object) -> None:
        response = client_logged.post(reverse("cw-send"), {
            "text": "73 <SK>", "wpm": 24, "tone_hz": 700,
        })
        assert response.status_code == 302
        session = CWSession.objects.get(user=user)
        assert session.direction == CWSession.Direction.SENT
        assert session.wpm == 24
        assert session.has_audio
        assert session.can_replay


class TestSessionAudio:
    def test_streams_wav_for_synth_session(self, client_logged: Client, user: object) -> None:
        client_logged.post(reverse("cw-send"), {"text": "TEST", "wpm": 20, "tone_hz": 600})
        session = CWSession.objects.get(user=user)
        response = client_logged.get(reverse("cw-session-audio", args=[session.pk]))
        assert response.status_code == 200
        assert response["Content-Type"] == "audio/wav"
        assert response.content[:4] == b"RIFF"

    def test_denies_other_users_session(
        self, client: Client, client_logged: Client, user: object, other_user: object
    ) -> None:
        client_logged.post(reverse("cw-send"), {"text": "TEST", "wpm": 20, "tone_hz": 600})
        session = CWSession.objects.get(user=user)
        client.force_login(other_user)
        assert client.get(reverse("cw-session-audio", args=[session.pk])).status_code == 404


class TestPerUserScoping:
    def _make_session(self, owner: object) -> CWSession:
        return CWSession.objects.create(
            user=owner, direction="rx", source="synth", text="CQ", wpm=20, tone_hz=600,
        )

    def test_list_shows_only_own_sessions(
        self, client_logged: Client, user: object, other_user: object
    ) -> None:
        self._make_session(user)
        other = self._make_session(other_user)
        response = client_logged.get(reverse("cw/sessions-list"))
        ids = [obj.pk for obj in response.context["object_list"]]
        assert other.pk not in ids
        assert len(ids) == 1

    def test_detail_404s_for_other_users_session(
        self, client_logged: Client, other_user: object
    ) -> None:
        other = self._make_session(other_user)
        response = client_logged.get(reverse("cw/sessions-detail", args=[other.pk]))
        assert response.status_code == 404

    def test_delete_denied_for_other_users_session(
        self, client_logged: Client, other_user: object
    ) -> None:
        # The CRUD delete view converts the scoping 404 into an error-message
        # redirect; the security property is that the row survives.
        other = self._make_session(other_user)
        response = client_logged.post(reverse("cw/sessions-delete", args=[other.pk]))
        assert response.status_code == 302
        assert CWSession.objects.filter(pk=other.pk).exists()

    def test_owner_can_delete(self, client_logged: Client, user: object) -> None:
        session = self._make_session(user)
        response = client_logged.post(reverse("cw/sessions-delete", args=[session.pk]))
        assert response.status_code == 302
        assert not CWSession.objects.filter(pk=session.pk).exists()


class TestMonitor:
    def test_monitor_embeds_replayable_sessions(self, client_logged: Client) -> None:
        client_logged.post(reverse("cw-decode"), {
            "mode": "practice", "text": "SOS", "wpm": 20, "tone_hz": 600,
        })
        response = client_logged.get(reverse("cw-monitor"))
        assert response.status_code == 200
        assert "cw-sessions-data" in response.content.decode()

    def test_monitor_empty_state(self, client_logged: Client) -> None:
        response = client_logged.get(reverse("cw-monitor"))
        assert response.status_code == 200
        assert "Nothing on the tape yet" in response.content.decode()
