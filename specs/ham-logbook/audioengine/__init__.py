"""audioengine — a hardware-independent audio decoding layer for the station
manager. CW is the first engine; the same seam hosts fldigi/WSJT-X taps and a
future ML decoder.
"""
from .events import CharEvent, ElementEvent, KeyRun, DecodeResult
from .engine import Engine, AudioDemodulator, NetworkTapEngine, AudioEngineManager
from .sources import (AudioSource, ArraySource, SyntheticCWSource, WavFileSource,
                      SoundDeviceSource)
from .cw import CWDecoder, CWConfig, decode_array
from .synth import synthesize_cw, SynthResult
from . import morse

__all__ = [
    "CharEvent", "ElementEvent", "KeyRun", "DecodeResult",
    "Engine", "AudioDemodulator", "NetworkTapEngine", "AudioEngineManager",
    "AudioSource", "ArraySource", "SyntheticCWSource", "WavFileSource",
    "SoundDeviceSource",
    "CWDecoder", "CWConfig", "decode_array",
    "synthesize_cw", "SynthResult", "morse",
]
