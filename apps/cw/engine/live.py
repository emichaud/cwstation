"""Live monitoring — drive the engine from an open-ended audio source.

The loop is source-agnostic (tests feed it a finite `SyntheticCWSource`; the
`cw_monitor_live` command feeds it a `SoundDeviceSource`), so the real-time
path is regression-tested with no radio attached. A finite source ends the
loop naturally; an infinite one runs until KeyboardInterrupt.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .audio_io import detect_tone
from .cw import CWConfig, CWDecoder
from .events import CharEvent, DecodeResult
from .manager import AudioEngineManager, FloatArray
from .sources import AudioSource


def monitor_live(
    source: AudioSource,
    tone_hz: float | None = None,
    calibrate_s: float = 3.0,
    expected_wpm: float | None = None,
    on_char: Callable[[CharEvent], None] | None = None,
    on_tone: Callable[[float], None] | None = None,
    on_tick: Callable[[DecodeResult], None] | None = None,
) -> DecodeResult:
    """Decode CW from `source` until it ends (or Ctrl-C on a live stream).

    With `tone_hz=None`, the first `calibrate_s` seconds are buffered, the CW
    note is detected from their spectrum, and the buffered audio is then
    replayed through the decoder — nothing heard during calibration is lost
    (the same philosophy as the decoder's own WPM bootstrap).
    """
    fs = source.sample_rate
    blocks = source.blocks()
    buffered: list[FloatArray] = []

    if tone_hz is None:
        target = int(calibrate_s * fs)
        have = 0
        for blk in blocks:
            buffered.append(blk)
            have += len(blk)
            if have >= target:
                break
        tone_hz = detect_tone(np.concatenate(buffered), fs) if buffered else 600.0
        if on_tone:
            on_tone(tone_hz)

    decoder = CWDecoder(fs, CWConfig(tone_hz=tone_hz, expected_wpm=expected_wpm))
    mgr = AudioEngineManager(fs).add_demodulator(decoder)
    if on_char:
        mgr.subscribe(on_char)

    try:
        for blk in buffered:
            mgr.pump(blk)
            if on_tick:
                on_tick(decoder.result)
        for blk in blocks:
            mgr.pump(blk)
            if on_tick:
                on_tick(decoder.result)
    except KeyboardInterrupt:  # pragma: no cover - live-stream exit path
        pass
    mgr.finalize()
    decoder.result.tone_hz = tone_hz
    return decoder.result
