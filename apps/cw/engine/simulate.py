"""A simulated band — radio static with CW transmissions embedded in it.

`SimulatedBandSource` is an `AudioSource` that plays like a receiver on a
busy band: continuous noise at an adjustable level, with stations popping up
at random times, random pitches, random speeds, and random signal strengths.
It's the no-hardware stand-in for `SoundDeviceSource` — the AFC demo (each
new station appears at a different pitch for the decoder to chase) and the
squelch/level workbench.

Attributes `noise_level` and `paused_signals` may be mutated while the source
is running (the simulate command polls the operator's slider values and pokes
them between blocks).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from .manager import FloatArray
from .sources import AudioSource
from .synth import synthesize_cw

# What you actually hear on the band. Speeds/pitches are chosen per station.
DEFAULT_MESSAGES: tuple[str, ...] = (
    "CQ CQ CQ DE W1AW W1AW K",
    "CQ TEST DE K5TR K5TR TEST",
    "TU 5NN 73 <SK>",
    "QRL? DE N0CALL",
    "CQ DX CQ DX DE JA1NUT JA1NUT K",
    "R R UR 559 559 IN NH BT HW? <BK>",
    "GM OM UR RST 579 579 NAME IS ED ED <BT>",
    "73 GL ES GUD DX <AR>",
)


@dataclass
class SimTransmission:
    """One station's transmission, as scheduled on the simulated band."""

    text: str
    tone_hz: float
    wpm: float
    amplitude: float
    start_sample: int
    audio: FloatArray = field(repr=False)


class SimulatedBandSource(AudioSource):
    """Infinite noise + scheduled CW stations. Deterministic per seed."""

    def __init__(
        self,
        sample_rate: int = 8000,
        block_size: int = 512,
        noise_level: float = 0.08,
        tone_range: tuple[float, float] = (450.0, 950.0),
        wpm_range: tuple[float, float] = (14.0, 26.0),
        amplitude_range: tuple[float, float] = (0.25, 0.8),
        idle_range_s: tuple[float, float] = (1.5, 4.0),
        messages: tuple[str, ...] = DEFAULT_MESSAGES,
        seed: int = 0,
        duration_s: float | None = None,
        realtime: bool = False,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.noise_level = noise_level  # live-adjustable
        self.paused_signals = False  # live-adjustable: noise only, no stations
        self.tone_range = tone_range
        self.wpm_range = wpm_range
        self.amplitude_range = amplitude_range
        self.idle_range_s = idle_range_s
        self.messages = messages
        self.duration_s = duration_s
        self.realtime = realtime
        self._rng = np.random.default_rng(seed)
        self._pos = 0  # absolute sample position on the band
        self._current: SimTransmission | None = None
        self._next_start = self._pos + self._n(self._uniform(*idle_range_s))
        self.log: list[SimTransmission] = []  # every station that transmitted

    # -- helpers -------------------------------------------------------------
    def _n(self, seconds: float) -> int:
        return int(round(seconds * self.sample_rate))

    def _uniform(self, lo: float, hi: float) -> float:
        return float(self._rng.uniform(lo, hi))

    def _schedule_next(self) -> None:
        text = str(self._rng.choice(list(self.messages)))
        tone = self._uniform(*self.tone_range)
        wpm = self._uniform(*self.wpm_range)
        amp = self._uniform(*self.amplitude_range)
        synth = synthesize_cw(
            text, wpm=wpm, tone_hz=tone, sample_rate=self.sample_rate,
            amplitude=amp, seed=int(self._rng.integers(0, 2**31)),
        )
        self._current = SimTransmission(
            text=text, tone_hz=tone, wpm=wpm, amplitude=amp,
            start_sample=self._next_start, audio=synth.audio,
        )
        self.log.append(self._current)

    # -- AudioSource ---------------------------------------------------------
    def blocks(self) -> Iterator[FloatArray]:
        end = None if self.duration_s is None else self._n(self.duration_s)
        while end is None or self._pos < end:
            block = self._rng.normal(
                0.0, max(self.noise_level, 0.0) or 1e-6, self.block_size
            ).astype(np.float32)

            if not self.paused_signals:
                if self._current is None and self._pos + self.block_size >= self._next_start:
                    self._schedule_next()
                tx = self._current
                if tx is not None:
                    # overlap of [pos, pos+block) with the transmission
                    tx_end = tx.start_sample + len(tx.audio)
                    a = max(self._pos, tx.start_sample)
                    b = min(self._pos + self.block_size, tx_end)
                    if a < b:
                        block[a - self._pos : b - self._pos] += tx.audio[
                            a - tx.start_sample : b - tx.start_sample
                        ]
                    if self._pos + self.block_size >= tx_end:
                        self._current = None
                        self._next_start = tx_end + self._n(self._uniform(*self.idle_range_s))

            self._pos += self.block_size
            if self.realtime:
                time.sleep(self.block_size / self.sample_rate)
            yield block

    @property
    def truth(self) -> str:
        """Everything that has been transmitted so far, in order."""
        return " ".join(tx.text for tx in self.log)
