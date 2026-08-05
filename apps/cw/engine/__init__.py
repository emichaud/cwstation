"""cw.engine — a Django-free, fully typed CW (Morse) audio layer.

Nothing in this package may import Django. The single integration point with
the web app is `bridge.CWLogBridge.on_qso_ready()`.

Every engine speaks one event contract (`CharEvent` / `ElementEvent` /
`KeyRun`), so consumers never learn which engine produced a character. Adding
a mode later means writing one class and registering it with
`AudioEngineManager`.
"""
from __future__ import annotations

from .cw import CWConfig, CWDecoder, decode_array
from .events import CharEvent, DecodeResult, ElementEvent, KeyRun
from .manager import AudioDemodulator, AudioEngineManager, NetworkTapEngine
from .sources import ArraySource, AudioSource, SyntheticCWSource, WavFileSource
from .synth import SynthResult, dit_seconds, synthesize_cw

__all__ = [
    "ArraySource",
    "AudioDemodulator",
    "AudioEngineManager",
    "AudioSource",
    "CWConfig",
    "CWDecoder",
    "CharEvent",
    "DecodeResult",
    "ElementEvent",
    "KeyRun",
    "NetworkTapEngine",
    "SynthResult",
    "SyntheticCWSource",
    "WavFileSource",
    "decode_array",
    "dit_seconds",
    "synthesize_cw",
]
