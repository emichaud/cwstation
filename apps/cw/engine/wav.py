"""float32 <-> WAV conversion helpers.

Used by the Send page (synthesized audio -> downloadable/playable WAV) and the
Decode page (uploaded WAV -> float samples). Standard-library `wave` only —
16-bit PCM out, 8/16/32-bit PCM mono/stereo in.
"""
from __future__ import annotations

import io
import wave
from typing import BinaryIO

import numpy as np

from .manager import FloatArray


def wav_bytes_from_float32(audio: FloatArray, sample_rate: int) -> bytes:
    """Encode float32 audio (-1..1) as a mono 16-bit PCM WAV file."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def float32_from_wav(stream: BinaryIO) -> tuple[FloatArray, int]:
    """Decode a WAV file-like object into (float32 mono audio, sample_rate).

    Accepts 8/16/32-bit PCM, mono or multi-channel (channels are averaged).
    """
    with wave.open(stream, "rb") as wf:
        fs = wf.getframerate()
        n = wf.getnframes()
        ch = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(n)
    dtypes: dict[int, type[np.unsignedinteger] | type[np.signedinteger]] = {
        1: np.uint8, 2: np.int16, 4: np.int32,
    }
    if width not in dtypes:
        raise ValueError(f"Unsupported WAV sample width: {width * 8}-bit")
    dtype = dtypes[width]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if dtype is np.uint8:
        data = (data - 128.0) / 128.0
    else:
        data = data / float(np.iinfo(dtype).max)
    return data.astype(np.float32), fs
