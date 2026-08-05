"""Structured events emitted by audio engines.

Every engine (CW now; fldigi/WSJT-X taps and an ML decoder later) speaks in
these events, so the manager, the logbook bridge, and the live view never need
to know which engine produced a given character. This is the contract that
makes the audio layer a general seam rather than a CW-specific hack.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


@dataclass
class KeyRun:
    """A completed run of the keyed line being on (mark) or off (space)."""
    on: bool
    t_start: float          # seconds from stream start
    dur_ms: float


@dataclass
class ElementEvent:
    """A single decoded Morse element."""
    kind: Literal[".", "-"]
    t_start: float
    dur_ms: float


@dataclass
class CharEvent:
    """A fully decoded character (or prosign / word space)."""
    char: str               # decoded text, " " for a word gap, "\uFFFD" if unknown
    morse: str              # the dot/dash string it came from ("" for a space)
    t_start: float
    t_end: float
    wpm: float              # decoder's speed estimate at this point
    snr_db: float           # rough signal-to-noise estimate at this point
    confidence: float = 1.0 # 0..1, how cleanly the timing matched


@dataclass
class DecodeResult:
    """Everything a decode pass produced — text plus the telemetry the modern
    view renders (keying trace, WPM track, envelope)."""
    text: str = ""
    engine: str = "cw"
    chars: list[CharEvent] = field(default_factory=list)
    elements: list[ElementEvent] = field(default_factory=list)
    key_runs: list[KeyRun] = field(default_factory=list)
    envelope_t: list[float] = field(default_factory=list)   # sample times (s)
    envelope_mag: list[float] = field(default_factory=list) # normalized 0..1
    envelope_thr: list[float] = field(default_factory=list) # threshold 0..1
    sample_rate: int = 0
    tone_hz: float = 0.0
    wpm_final: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)
