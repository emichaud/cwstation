"""Hardware-free regression tests for the CW engine: synthesize CW at a known
speed/text, decode it, and assert we got it back. This loop is what lets the
audio layer be developed with no radio attached.
"""
from __future__ import annotations

import io

import numpy as np
import pytest

from apps.cw.engine import (
    AudioEngineManager,
    CWConfig,
    CWDecoder,
    SyntheticCWSource,
    WavFileSource,
    decode_array,
    morse,
    synthesize_cw,
)
from apps.cw.engine.bridge import CWLogBridge, QSODraft, extract_callsigns
from apps.cw.engine.export import session_from_result
from apps.cw.engine.wav import float32_from_wav, wav_bytes_from_float32


def char_accuracy(got: str, want: str) -> float:
    return sum(1 for a, b in zip(got, want) if a == b) / max(len(want), 1)


class TestMorseTables:
    def test_roundtrip_symbols(self):
        for ch, code in morse.CHAR_TO_MORSE.items():
            if not ch.startswith("<"):
                assert morse.decode_symbol(code) == ch

    def test_encode_text(self):
        assert morse.encode_text("SOS") == ["...", "---", "..."]
        assert " " in morse.encode_text("A B")

    def test_encode_inline_prosign(self):
        # <AR> encodes as one run-together symbol, not four characters
        assert morse.encode_text("<AR>") == [".-.-."]
        assert morse.encode_text("73 <SK>") == ["--...", "...--", " ", "...-.-"]

    def test_unknown_symbol_maps_to_replacement(self):
        assert morse.decode_symbol("......--..") == morse.UNKNOWN_CHAR


class TestCleanDecode:
    CASES = [
        "PARIS PARIS",
        "CQ CQ DE N0CALL K",
        "THE QUICK BROWN FOX 73",
        "599 TU 73 GL",
        "SOS SOS",
    ]

    @pytest.mark.parametrize("text", CASES)
    def test_blind_bootstrap_exact(self, text: str):
        # no expected_wpm supplied — decoder must recover speed on its own
        r = synthesize_cw(text, wpm=20, tone_hz=600, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
        assert res.text == text.upper()

    @pytest.mark.parametrize("wpm", [10, 15, 20, 25, 30])
    def test_speed_sweep(self, wpm: int):
        r = synthesize_cw("PARIS", wpm=wpm, tone_hz=700, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=700))
        assert res.text == "PARIS"
        assert res.wpm_final == pytest.approx(wpm, abs=2.5)


class TestNoisyDecode:
    @pytest.mark.parametrize("snr", [20, 12, 6])
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_noise_sweep(self, snr: int, seed: int):
        want = "CQ TEST DE W1AW"
        r = synthesize_cw(want, wpm=20, tone_hz=600, sample_rate=8000,
                          snr_db=snr, seed=seed)
        res = decode_array(r.audio, r.sample_rate,
                           CWConfig(tone_hz=600, expected_wpm=20))
        assert char_accuracy(res.text, want) >= 0.85, f"snr={snr} seed={seed}: {res.text!r}"


class TestStreamingEquivalence:
    def test_chunked_matches_oneshot(self):
        r = synthesize_cw("CQ DE K1ABC K", wpm=18, tone_hz=600, sample_rate=8000)
        oneshot = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600)).text
        # feed in awkward chunk sizes to prove state carries across calls
        dec = CWDecoder(r.sample_rate, CWConfig(tone_hz=600))
        for i in range(0, len(r.audio), 333):
            dec.process(r.audio[i : i + 333])
        assert dec.finalize().text == oneshot

    def test_flush_is_idempotent(self):
        r = synthesize_cw("TEST", wpm=20, tone_hz=600, sample_rate=8000)
        dec = CWDecoder(r.sample_rate, CWConfig(tone_hz=600))
        dec.process(r.audio)
        first = dec.flush()
        assert first  # emitted something
        assert dec.flush() == []


class TestManagerAndEvents:
    def test_manager_dispatches_char_events(self):
        collected: list[str] = []
        mgr = AudioEngineManager(sample_rate=8000)
        mgr.add_demodulator(CWDecoder(8000, CWConfig(tone_hz=600)))
        mgr.subscribe(lambda ev: collected.append(ev.char))
        src = SyntheticCWSource("SOS", wpm=20, tone_hz=600, sample_rate=8000)
        mgr.run_source(src)
        assert "".join(collected).strip() == "SOS"

    def test_word_gaps_are_stored_events(self):
        # word spaces must be in result.chars, or replays/copy lose word gaps
        r = synthesize_cw("CQ DE", wpm=20, tone_hz=600, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
        assert "".join(c.char for c in res.chars).strip() == "CQ DE"

    def test_char_events_carry_telemetry(self):
        r = synthesize_cw("CQ", wpm=20, tone_hz=600, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
        assert res.chars, "no characters decoded"
        for ev in res.chars:
            if ev.char != " ":
                assert ev.wpm > 0
                assert 0.0 <= ev.confidence <= 1.0


class TestWavRoundTrip:
    def test_wav_bytes_roundtrip(self):
        r = synthesize_cw("TEST DE AB1CD", wpm=20, tone_hz=600, sample_rate=8000)
        blob = wav_bytes_from_float32(r.audio, r.sample_rate)
        data, fs = float32_from_wav(io.BytesIO(blob))
        assert fs == 8000
        assert len(data) == len(r.audio)
        res = decode_array(data, fs, CWConfig(tone_hz=600))
        assert res.text == "TEST DE AB1CD"

    def test_wav_file_source_stream(self):
        r = synthesize_cw("TEST", wpm=20, tone_hz=600, sample_rate=8000)
        blob = wav_bytes_from_float32(r.audio, r.sample_rate)
        src = WavFileSource(io.BytesIO(blob))
        dec = CWDecoder(src.sample_rate, CWConfig(tone_hz=600))
        for blk in src.blocks():
            dec.process(blk)
        assert dec.finalize().text == "TEST"

    def test_stereo_wav_is_downmixed(self):
        r = synthesize_cw("E E", wpm=20, tone_hz=600, sample_rate=8000)
        stereo = np.repeat(r.audio[:, None], 2, axis=1).reshape(-1)
        import wave

        buf = io.BytesIO()
        pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(pcm.tobytes())
        buf.seek(0)
        data, fs = float32_from_wav(buf)
        assert len(data) == len(r.audio)

    def test_unsupported_width_raises(self):
        buf = io.BytesIO()
        import wave

        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)  # 24-bit — unsupported
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00\x00" * 16)
        buf.seek(0)
        with pytest.raises(ValueError, match="24-bit"):
            float32_from_wav(buf)


class TestRecordingFormats:
    FIXTURE = "apps/cw/tests/fixtures/test_de_ab1cd_20wpm_700hz.mp3"

    def test_mp3_loads_and_decodes(self):
        from apps.cw.engine import load_audio

        with open(self.FIXTURE, "rb") as f:
            data, fs = load_audio(f)
        assert fs == 8000
        res = decode_array(data, fs, CWConfig(tone_hz=700))
        assert res.text == "TEST DE AB1CD"

    def test_audio_file_source_reads_mp3_path(self):
        from apps.cw.engine import AudioFileSource

        src = AudioFileSource(self.FIXTURE)
        dec = CWDecoder(src.sample_rate, CWConfig(tone_hz=700))
        for blk in src.blocks():
            dec.process(blk)
        assert dec.finalize().text == "TEST DE AB1CD"

    def test_garbage_raises_value_error(self):
        from apps.cw.engine import load_audio

        with pytest.raises(ValueError, match="WAV, MP3, FLAC, OGG"):
            load_audio(io.BytesIO(b"definitely not audio data" * 10))


class TestToneDetection:
    @pytest.mark.parametrize("tone", [450, 600, 750, 900])
    def test_detects_synth_tone(self, tone: int):
        from apps.cw.engine import detect_tone

        r = synthesize_cw("CQ CQ CQ DE N0CALL", wpm=20, tone_hz=tone, sample_rate=8000)
        got = detect_tone(r.audio, r.sample_rate)
        assert got == pytest.approx(tone, abs=15)

    def test_detected_tone_decodes_blind(self):
        from apps.cw.engine import detect_tone

        r = synthesize_cw("CQ DE W1AW", wpm=20, tone_hz=820, sample_rate=8000)
        tone = detect_tone(r.audio, r.sample_rate)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=tone))
        assert res.text == "CQ DE W1AW"

    def test_short_or_silent_audio_falls_back(self):
        from apps.cw.engine import detect_tone

        assert detect_tone(np.zeros(100, dtype=np.float32), 8000) == 600.0
        assert detect_tone(np.zeros(80000, dtype=np.float32), 8000) == 600.0


class TestEnvelopeDecimation:
    def test_long_envelope_is_capped(self):
        r = synthesize_cw("CQ CQ CQ DE N0CALL N0CALL K", wpm=20, tone_hz=600, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
        session = session_from_result(res, max_env_points=50)
        assert len(session["env_t"]) <= 51
        assert len(session["env_t"]) == len(session["env_mag"])
        # exact data untouched
        assert len(session["chars"]) == len(res.chars)
        assert len(session["key_runs"]) == len(res.key_runs)


class TestBridge:
    def test_extracts_callsign_and_rst(self):
        drafts: list[QSODraft] = []
        bridge = CWLogBridge()
        bridge.on_qso_ready = drafts.append  # type: ignore[method-assign]

        mgr = AudioEngineManager(8000)
        mgr.add_demodulator(CWDecoder(8000, CWConfig(tone_hz=600)))
        mgr.subscribe(bridge.on_char)
        # trailing spaces from the word-gap logic close the draft after decode
        src = SyntheticCWSource("CQ TEST DE W1AW 599    ", wpm=20, tone_hz=600)
        mgr.run_source(src)
        bridge._flush_word()

        assert "W1AW" in bridge.draft.callsigns
        assert "599" in bridge.draft.rst

    def test_extract_callsigns_helper(self):
        found = extract_callsigns("cq cq de n0call n0call k w1aw")
        assert found == ["N0CALL", "W1AW"]


class TestSessionExport:
    def test_session_shape(self):
        r = synthesize_cw("CQ", wpm=20, tone_hz=600, sample_rate=8000)
        res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
        session = session_from_result(res, truth="CQ")
        assert session["meta"]["decoded"] == "CQ"
        assert session["meta"]["truth"] == "CQ"
        assert session["key_runs"], "expected keyed runs for the tape view"
        assert session["chars"][0]["c"] == "C"
