"""A modern, streaming CW (Morse) decoder.

Design goals that make this a "modern view" rather than a port of the classic
OZ1JHM Arduino sketch:

* Vectorized single-bin DFT (Goertzel-equivalent) tone detection using numpy,
  with a 3-bin comparison around the target tone for frequency tolerance.
* Smoothed envelope + fast-attack / slow-release peak & floor trackers, so the
  keyed/unkeyed decision is robust to band noise.
* Fully adaptive: continuously estimates WPM from element timing, with a
  two-phase bootstrap that recovers dit length from the opening characters
  before committing any decode (so the first character isn't lost).
* Streaming: process() carries state across chunks (live sound card) or runs
  once over a whole file.
* Emits structured events (elements, characters, WPM, SNR, confidence) plus the
  telemetry the live view renders.

Pipeline: audio -> tone magnitude -> smoothed envelope -> adaptive keyed state
-> key runs (marks/spaces) -> elements (dit/dah) -> characters.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .events import CharEvent, DecodeResult, ElementEvent, KeyRun
from .morse import decode_symbol
from .engine import AudioDemodulator


@dataclass
class CWConfig:
    tone_hz: float = 600.0
    block_ms: float = 4.0
    expected_wpm: float | None = None      # optional prior; decoder still adapts
    min_wpm: float = 5.0
    max_wpm: float = 60.0
    env_smooth: float = 0.3                 # EMA alpha for magnitude smoothing
    attack: float = 0.5                     # peak/floor fast-follow toward extreme
    peak_release: float = 0.003             # slow peak decay per block
    floor_rise: float = 0.004               # slow floor rise per block
    hysteresis: float = 0.2                 # fraction of range for on/off band
    debounce_ms: float = 3.0                # min consistent time to flip state
    boot_marks: int = 8                     # marks to buffer before bootstrapping


class CWDecoder(AudioDemodulator):
    name = "cw"

    def __init__(self, sample_rate: int, config: CWConfig | None = None):
        self.fs = sample_rate
        self.cfg = config or CWConfig()
        self.block = max(8, int(round(self.cfg.block_ms / 1000.0 * self.fs)))
        self._build_twiddles()
        self.reset()

    def _build_twiddles(self):
        N = self.block
        n = np.arange(N)
        rows = []
        for df in (-1, 0, 1):
            f = self.cfg.tone_hz + df * (self.fs / N)
            rows.append(np.exp(-2j * np.pi * (f / self.fs) * n))
        self._tw = np.array(rows)

    def reset(self):
        c = self.cfg
        self._carry = np.zeros(0, dtype=np.float32)
        self._t_samples = 0
        # envelope / threshold state
        self._env = None
        self._peak = None
        self._floor = None
        self._state = False           # committed keyed state
        self._cand = False            # candidate state for debounce
        self._cand_since = 0.0
        self._run_start_s = 0.0
        self._debounce_n = max(1, int(round(c.debounce_ms / c.block_ms)))
        self._cand_count = 0
        # timing / decode state
        self._dit = (1.2 / c.expected_wpm) if c.expected_wpm else None
        self._boot_runs: list[tuple[bool, float, float]] = []  # (on, t_start, dur_s)
        self._boot_marks: list[float] = []
        self._cur = ""
        self._cur_start = 0.0
        self._wpm = float(c.expected_wpm or 0.0)
        self._snr = 0.0
        self._flushed = False
        self.result = DecodeResult(sample_rate=self.fs, tone_hz=c.tone_hz)

    # -- public --------------------------------------------------------------
    def process(self, samples: np.ndarray) -> list[CharEvent]:
        buf = np.concatenate([self._carry, np.asarray(samples, dtype=np.float32)])
        nb = len(buf) // self.block
        out: list[CharEvent] = []
        for b in range(nb):
            blk = buf[b * self.block:(b + 1) * self.block]
            mag = float(np.abs(self._tw @ blk).max()) / self.block
            out += self._step(mag, self._t_samples / self.fs)
            self._t_samples += self.block
        self._carry = buf[nb * self.block:]
        return out

    def flush(self) -> list[CharEvent]:
        """End-of-stream flush: close a dangling mark, finish bootstrap, and
        emit the final pending character. Idempotent."""
        if getattr(self, "_flushed", False):
            return []
        now = self._t_samples / self.fs
        dur = now - self._run_start_s
        if self._state:
            self._on_mark_end(self._run_start_s, dur)
            self._state = False
        out: list[CharEvent] = []
        if self._dit is None and self._boot_marks:
            self._dit = self._estimate_dit()
            out += self._replay_boot()
        out += self._flush(now, word=True)
        self.result.text = self.result.text.rstrip()
        self.result.wpm_final = round(self._wpm, 1)
        self._flushed = True
        return out

    def finalize(self) -> DecodeResult:
        self.flush()
        return self.result

    # -- stage: envelope + adaptive keyed state ------------------------------
    def _step(self, mag: float, t_s: float) -> list[CharEvent]:
        c = self.cfg
        # smooth
        self._env = mag if self._env is None else self._env + c.env_smooth * (mag - self._env)
        e = self._env
        if self._peak is None:
            self._peak = self._floor = e
        # fast-attack / slow-release trackers
        self._peak = e if e > self._peak else self._peak + c.peak_release * (e - self._peak)
        self._floor = e if e < self._floor else self._floor + c.floor_rise * (e - self._floor)
        rng = max(self._peak - self._floor, 1e-9)
        thr = self._floor + 0.5 * rng
        hi = thr + c.hysteresis * rng
        lo = thr - c.hysteresis * rng
        self._snr = 10.0 * np.log10((self._peak + 1e-9) / (self._floor + 1e-9))

        # telemetry (normalized 0..1)
        self.result.envelope_t.append(round(t_s, 4))
        self.result.envelope_mag.append(round(float((e - self._floor) / rng), 4))
        self.result.envelope_thr.append(round(0.5, 4))

        # candidate state with hysteresis
        cand = self._cand
        if not self._state and e > hi:
            cand = True
        elif self._state and e < lo:
            cand = False
        # debounce: require the candidate to persist before committing
        if cand != self._state:
            if cand == self._cand:
                self._cand_count += 1
            else:
                self._cand = cand
                self._cand_count = 1
            if self._cand_count >= self._debounce_n:
                return self._commit(cand, t_s)
        else:
            self._cand = self._state
            self._cand_count = 0
        return []

    def _commit(self, new_state: bool, t_s: float) -> list[CharEvent]:
        dur = t_s - self._run_start_s
        out: list[CharEvent] = []
        if self._state:          # a MARK just ended
            out += self._on_mark_end(self._run_start_s, dur)
        else:                    # a SPACE just ended
            out += self._on_space_end(dur)
        self._run_start_s = t_s
        self._state = new_state
        self._cand_count = 0
        return out

    # -- stage: runs -> elements -> characters -------------------------------
    def _on_mark_end(self, t_start: float, dur: float) -> list[CharEvent]:
        self.result.key_runs.append(KeyRun(True, round(t_start, 4), round(dur * 1000, 2)))
        if self._dit is None:
            self._boot_runs.append((True, t_start, dur))
            self._boot_marks.append(dur)
            if len(self._boot_marks) >= self.cfg.boot_marks:
                self._dit = self._estimate_dit()
                return self._replay_boot()
            return []
        self._add_element(t_start, dur)
        return []

    def _on_space_end(self, dur: float) -> list[CharEvent]:
        # only meaningful once we have some marks
        if self._dit is None:
            if self._boot_marks:              # buffer inter-mark spaces too
                self._boot_runs.append((False, self._run_start_s, dur))
            return []
        return self._space_logic(dur, self._run_start_s + dur)

    def _add_element(self, t_start: float, dur: float):
        kind = "." if dur < 2 * self._dit else "-"
        target = dur if kind == "." else dur / 3.0
        self._dit = 0.8 * self._dit + 0.2 * target      # adapt
        self._update_wpm()
        if not self._cur:
            self._cur_start = t_start
        self._cur += kind
        self.result.elements.append(ElementEvent(kind, round(t_start, 4), round(dur * 1000, 2)))

    def _space_logic(self, dur: float, t_end: float) -> list[CharEvent]:
        if dur < 2 * self._dit:
            return []                               # intra-character gap
        if dur < 5 * self._dit:
            return self._flush(t_end, word=False)   # character gap
        return self._flush(t_end, word=True)        # word gap

    def _flush(self, t_end: float, word: bool) -> list[CharEvent]:
        out: list[CharEvent] = []
        if self._cur:
            ch = decode_symbol(self._cur)
            ev = CharEvent(ch, self._cur, round(self._cur_start, 4), round(t_end, 4),
                           round(self._wpm, 1), round(self._snr, 1),
                           0.4 if ch == "\ufffd" else 1.0)
            self.result.chars.append(ev)
            self.result.text += ch
            out.append(ev)
            self._cur = ""
        if word and self.result.text and not self.result.text.endswith(" "):
            self.result.text += " "
            out.append(CharEvent(" ", "", round(t_end, 4), round(t_end, 4),
                                 round(self._wpm, 1), round(self._snr, 1), 1.0))
        return out

    # -- bootstrap -----------------------------------------------------------
    def _estimate_dit(self) -> float:
        marks = np.array(self._boot_marks)
        lo = marks.min()
        dit_cluster = marks[marks <= 1.6 * lo]      # marks near the minimum = dits
        return float(np.median(dit_cluster))

    def _replay_boot(self) -> list[CharEvent]:
        self._update_wpm()
        out: list[CharEvent] = []
        for on, t_start, dur in self._boot_runs:
            if on:
                self._add_element(t_start, dur)
            else:
                out += self._space_logic(dur, t_start + dur)
        self._boot_runs.clear()
        self._boot_marks.clear()
        return out

    def _update_wpm(self):
        if self._dit:
            self._wpm = float(np.clip(1.2 / self._dit, self.cfg.min_wpm, self.cfg.max_wpm))


def decode_array(samples: np.ndarray, sample_rate: int,
                 config: CWConfig | None = None) -> DecodeResult:
    dec = CWDecoder(sample_rate, config)
    dec.process(samples)
    return dec.finalize()
