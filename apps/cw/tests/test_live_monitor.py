"""Live-monitor tests — the real-time loop driven by a finite synthetic
source, so the radio path is regression-tested with no hardware attached."""
from __future__ import annotations

import importlib.util

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cw import services
from apps.cw.engine import SyntheticCWSource
from apps.cw.engine.events import CharEvent
from apps.cw.engine.live import monitor_live
from apps.cw.models import CWSession

User = get_user_model()


class TestMonitorLive:
    def test_decodes_stream_with_fixed_tone(self):
        src = SyntheticCWSource("CQ CQ DE N0CALL K", wpm=20, tone_hz=600, block_size=512)
        result = monitor_live(src, tone_hz=600)
        assert result.text == "CQ CQ DE N0CALL K"

    def test_auto_tone_calibration_loses_no_audio(self):
        # tone unknown; the calibration buffer must be replayed so the very
        # first characters (sent during calibration) still decode
        src = SyntheticCWSource("CQ TEST DE W1AW", wpm=20, tone_hz=750, block_size=512)
        tones: list[float] = []
        result = monitor_live(src, tone_hz=None, calibrate_s=2.0, on_tone=tones.append)
        assert tones and tones[0] == pytest.approx(750, abs=15)
        assert result.tone_hz == pytest.approx(750, abs=15)
        assert result.text == "CQ TEST DE W1AW"

    def test_chars_stream_through_callback(self):
        src = SyntheticCWSource("SOS", wpm=20, tone_hz=600, block_size=512)
        seen: list[CharEvent] = []
        monitor_live(src, tone_hz=600, on_char=seen.append)
        assert "".join(ev.char for ev in seen).strip() == "SOS"

    def test_short_silent_stream_returns_empty(self):
        import numpy as np

        from apps.cw.engine import ArraySource

        src = ArraySource(np.zeros(8000, dtype=np.float32), 8000, block_size=512)
        result = monitor_live(src, tone_hz=None, calibrate_s=0.5)
        assert result.text == ""


@pytest.mark.django_db
class TestSaveLiveSession:
    def test_persists_replayable_session(self):
        user = User.objects.create_user(username="op", password="x")
        src = SyntheticCWSource("CQ DE K1ABC", wpm=20, tone_hz=600, block_size=512)
        result = monitor_live(src, tone_hz=600)
        session = services.save_live_session(user, result, 600.0)
        assert session.source == CWSession.Source.LIVE
        assert session.direction == CWSession.Direction.RECEIVED
        assert session.text == "CQ DE K1ABC"
        assert "K1ABC" in session.callsigns
        assert session.can_replay
        assert not session.has_audio  # live audio is never stored


@pytest.mark.skipif(
    importlib.util.find_spec("sounddevice") is not None,
    reason="sounddevice installed — the missing-dependency hint can't trigger",
)
class TestCommandGuard:
    def test_missing_sounddevice_gives_install_hint(self):
        with pytest.raises(CommandError, match="uv sync --extra live"):
            call_command("cw_monitor_live", "--list-devices")
