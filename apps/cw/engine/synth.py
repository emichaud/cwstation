"""Synthesize CW audio from text — the key to developing and regression-testing
the whole audio layer with zero radio hardware, and the transmit path for the
Send page (text -> keyed sidetone WAV).

`synthesize_cw("CQ CQ DE N0CALL K", wpm=20)` returns a float32 array plus the
ground-truth keying, so a test can assert the decoder recovered exactly what
went in, and the live view can be driven from a known signal.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .manager import FloatArray
from .morse import encode_text


@dataclass
class SynthResult:
    audio: FloatArray  # float32, roughly -1..1
    sample_rate: int
    tone_hz: float
    wpm: float
    text: str
    keying: FloatArray  # ground-truth 0/1 envelope, same length as audio


def dit_seconds(wpm: float) -> float:
    """Standard PARIS timing: one dit = 1200 ms / WPM."""
    return 1.2 / wpm


def synthesize_cw(
    text: str,
    wpm: float = 20.0,
    tone_hz: float = 600.0,
    sample_rate: int = 8000,
    snr_db: float | None = None,
    rise_ms: float = 5.0,
    amplitude: float = 0.7,
    lead_ms: float = 60.0,
    seed: int | None = 0,
) -> SynthResult:
    """Build a CW audio buffer for `text`.

    * Timing follows PARIS: dit=1u, dah=3u, intra-element gap=1u, char gap=3u,
      word gap=7u, where u = dit_seconds(wpm).
    * Edges get a raised-cosine ramp so there are no key clicks — this is what
      `cwwav`/`ebook2cw` do to make honest, comfortable CW.
    * `snr_db` adds white noise at a controlled ratio to exercise the decoder's
      adaptive threshold; None means noiseless.
    """
    unit = dit_seconds(wpm)
    fs = sample_rate

    def n_samples(seconds: float) -> int:
        return int(round(seconds * fs))

    # Build the keying envelope (0/1) as a list of (state, n_samples) segments.
    segments: list[tuple[int, int]] = []
    segments.append((0, n_samples(lead_ms / 1000.0)))  # leading silence

    symbols = encode_text(text)
    for sym in symbols:
        if sym == " ":
            # word gap: we already emit a 3u char gap after each char, so add
            # 4u more to reach 7u total.
            segments.append((0, n_samples(4 * unit)))
            continue
        for j, el in enumerate(sym):
            length = unit if el == "." else 3 * unit
            segments.append((1, n_samples(length)))
            if j < len(sym) - 1:
                segments.append((0, n_samples(unit)))  # intra-element gap
        # gap after a character (unless a word space follows, handled above)
        segments.append((0, n_samples(3 * unit)))

    segments.append((0, n_samples(lead_ms / 1000.0)))  # trailing silence

    total = sum(n for _, n in segments)
    keying = np.zeros(total, dtype=np.float32)
    idx = 0
    for state, n in segments:
        if state:
            keying[idx : idx + n] = 1.0
        idx += n

    # Raised-cosine edge shaping on the keying to avoid clicks.
    ramp_n = max(1, n_samples(rise_ms / 1000.0))
    shaped = _shape_edges(keying, ramp_n)

    # Modulate the tone.
    t = np.arange(total, dtype=np.float32) / fs
    tone = np.sin(2 * np.pi * tone_hz * t).astype(np.float32)
    audio = amplitude * shaped * tone

    if snr_db is not None:
        rng = np.random.default_rng(seed)
        sig_power = np.mean((amplitude * shaped) ** 2) / 2 + 1e-12
        noise_power = sig_power / (10 ** (snr_db / 10))
        audio = audio + rng.normal(0, np.sqrt(noise_power), total).astype(np.float32)

    return SynthResult(
        audio=audio.astype(np.float32),
        sample_rate=fs,
        tone_hz=tone_hz,
        wpm=wpm,
        text=text.upper(),
        keying=keying,
    )


def _shape_edges(env: FloatArray, ramp_n: int) -> FloatArray:
    """Apply a raised-cosine ramp on 0->1 and 1->0 transitions of a 0/1 env."""
    if ramp_n <= 1:
        return env
    out = env.copy()
    ramp_up = 0.5 * (1 - np.cos(np.linspace(0, np.pi, ramp_n)))
    ramp_dn = ramp_up[::-1]
    edges = np.diff(env)
    rising = np.where(edges > 0)[0] + 1
    falling = np.where(edges < 0)[0] + 1
    for r in rising:
        # np.where gives numpy ints; slice maths wants plain int
        s = slice(int(r), min(int(r) + ramp_n, len(out)))
        out[s] = ramp_up[: s.stop - s.start]
    for f in falling:
        s = slice(int(f), min(int(f) + ramp_n, len(out)))
        out[s] = ramp_dn[: s.stop - s.start]
    return out
