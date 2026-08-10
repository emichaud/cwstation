"""The squelch gate, exercised against real off-air audio.

The synthesized tests in `test_engine.py` prove the decoder on clean signals;
these two fixtures are the other half — 40 m recorded through a public WebSDR
(SM2BYC, Sweden), one an empty channel and one a weak QSO. Ungated, the empty
channel decodes into hundreds of junk characters, because band noise crossing
the adaptive threshold looks exactly like short marks. That is the regression
these guard.

Fixtures are mono MP3 tagged 12 kHz (the receiver samples at 11999 Hz; the
0.008% relabel keeps MPEG happy and is far below CW timing tolerance).
"""
from __future__ import annotations

import pytest

from apps.cw.engine import CWConfig, decode_array, detect_tone, load_audio
from apps.cw.engine.bridge import extract_callsigns
from apps.cw.services import RECORDING_SQUELCH_DB

NOISE = "apps/cw/tests/fixtures/offair_40m_noise_only.mp3"
WEAK = "apps/cw/tests/fixtures/offair_40m_weak_cw.mp3"


def _decode(path: str, squelch_db: float):
    with open(path, "rb") as f:
        audio, fs = load_audio(f)
    tone = detect_tone(audio, fs)
    return decode_array(audio, fs, CWConfig(tone_hz=tone, squelch_db=squelch_db))


class TestSquelchOnRealAudio:
    def test_empty_band_floods_the_copy_when_ungated(self):
        """Guards the bug the gate exists for: noise alone spells letters."""
        res = _decode(NOISE, 0.0)
        assert len(res.chars) > 300

    def test_default_gate_suppresses_most_of_the_noise(self):
        ungated = len(_decode(NOISE, 0.0).chars)
        gated = len(_decode(NOISE, RECORDING_SQUELCH_DB).chars)
        # ~84% of the hash goes; the gate is a threshold, not a silence switch,
        # so a residue is expected and must not be asserted away.
        assert gated < ungated * 0.25

    @pytest.mark.parametrize("word", ["ALEX", "HW", "OK", "DR"])
    def test_weak_signal_still_copies_through_the_gate(self, word: str):
        """The gate must cost nothing on a real (weak) signal — this is what
        pins the default. At 5 dB and above these words start dropping out."""
        assert word in _decode(WEAK, RECORDING_SQUELCH_DB).text

    def test_gate_does_not_shorten_weak_signal_copy(self):
        ungated = len(_decode(WEAK, 0.0).chars)
        gated = len(_decode(WEAK, RECORDING_SQUELCH_DB).chars)
        assert gated >= ungated * 0.9


class TestCallsignsFromRealCopy:
    """The gate thins the noise but can't stop what survives it from spelling a
    callsign-shaped token — `O1E` on the weak fixture. Nothing loggable should
    come off either noisy recording, while real copy still yields its call."""

    def test_noisy_copy_yields_no_loggable_callsign(self):
        assert extract_callsigns(_decode(WEAK, RECORDING_SQUELCH_DB).text) == []

    def test_empty_band_yields_no_loggable_callsign(self):
        assert extract_callsigns(_decode(NOISE, RECORDING_SQUELCH_DB).text) == []

    def test_junk_token_is_still_in_the_copy_just_not_loggable(self):
        """The operator must keep seeing the raw copy — filtering happens at the
        loggable-callsign layer, not by editing what was heard."""
        text = _decode(WEAK, 0.0).text
        assert "O1E" in text
        assert "O1E" not in extract_callsigns(text)

    def test_clean_recording_still_yields_its_callsign(self):
        res = _decode("apps/cw/tests/fixtures/test_de_ab1cd_20wpm_700hz.mp3", 0.0)
        assert "AB1CD" in extract_callsigns(res.text)
