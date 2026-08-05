"""Hardware-free regression tests: synthesize CW at a known speed/text, decode
it, and assert we got it back. This is the loop that lets the audio layer be
developed with no radio.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audioengine import synthesize_cw, decode_array, CWConfig, morse
from audioengine.sources import SyntheticCWSource, WavFileSource
from audioengine.engine import AudioEngineManager
from audioengine.cw import CWDecoder


def char_accuracy(got: str, want: str) -> float:
    return sum(1 for a, b in zip(got, want) if a == b) / max(len(want), 1)


class TestMorseTables(unittest.TestCase):
    def test_roundtrip_symbols(self):
        for ch, code in morse.CHAR_TO_MORSE.items():
            if not ch.startswith("<"):
                self.assertEqual(morse.decode_symbol(code), ch)

    def test_encode_text(self):
        self.assertEqual(morse.encode_text("SOS"), ["...", "---", "..."])
        self.assertIn(" ", morse.encode_text("A B"))


class TestCleanDecode(unittest.TestCase):
    CASES = ["PARIS PARIS", "CQ CQ DE N0CALL K", "THE QUICK BROWN FOX 73",
             "599 TU 73 GL", "SOS SOS"]

    def test_blind_bootstrap_exact(self):
        # no expected_wpm supplied — decoder must recover speed on its own
        for text in self.CASES:
            r = synthesize_cw(text, wpm=20, tone_hz=600, sample_rate=8000)
            res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600))
            self.assertEqual(res.text, text.upper(), f"blind decode of {text!r}")

    def test_speed_sweep(self):
        for wpm in (10, 15, 20, 25, 30):
            r = synthesize_cw("PARIS", wpm=wpm, tone_hz=700, sample_rate=8000)
            res = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=700))
            self.assertEqual(res.text, "PARIS", f"{wpm} wpm")
            self.assertAlmostEqual(res.wpm_final, wpm, delta=2.5)


class TestNoisyDecode(unittest.TestCase):
    def test_noise_sweep(self):
        want = "CQ TEST DE W1AW"
        for snr in (20, 12, 6):
            for seed in (1, 2, 3):
                r = synthesize_cw(want, wpm=20, tone_hz=600, sample_rate=8000,
                                  snr_db=snr, seed=seed)
                res = decode_array(r.audio, r.sample_rate,
                                   CWConfig(tone_hz=600, expected_wpm=20))
                self.assertGreaterEqual(
                    char_accuracy(res.text, want), 0.85,
                    f"snr={snr} seed={seed}: {res.text!r}")


class TestStreamingEquivalence(unittest.TestCase):
    def test_chunked_matches_oneshot(self):
        r = synthesize_cw("CQ DE K1ABC K", wpm=18, tone_hz=600, sample_rate=8000)
        oneshot = decode_array(r.audio, r.sample_rate, CWConfig(tone_hz=600)).text
        # feed in awkward chunk sizes to prove state carries across calls
        dec = CWDecoder(r.sample_rate, CWConfig(tone_hz=600))
        for i in range(0, len(r.audio), 333):
            dec.process(r.audio[i:i + 333])
        self.assertEqual(dec.finalize().text, oneshot)


class TestManagerAndEvents(unittest.TestCase):
    def test_manager_dispatches_char_events(self):
        collected = []
        mgr = AudioEngineManager(sample_rate=8000)
        mgr.add_demodulator(CWDecoder(8000, CWConfig(tone_hz=600)))
        mgr.subscribe(lambda ev: collected.append(ev.char))
        src = SyntheticCWSource("SOS", wpm=20, tone_hz=600, sample_rate=8000)
        mgr.run_source(src)
        text = "".join(collected).strip()
        self.assertEqual(text, "SOS")


class TestWavRoundTrip(unittest.TestCase):
    def test_wav_file_source(self):
        import wave, tempfile, numpy as np
        r = synthesize_cw("TEST DE AB1CD", wpm=20, tone_hz=600, sample_rate=8000)
        pcm = (np.clip(r.audio, -1, 1) * 32767).astype("<i2")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(8000)
            wf.writeframes(pcm.tobytes())
        src = WavFileSource(path)
        dec = CWDecoder(src.sample_rate, CWConfig(tone_hz=600))
        for blk in src.blocks():
            dec.process(blk)
        os.unlink(path)
        self.assertEqual(dec.finalize().text, "TEST DE AB1CD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
