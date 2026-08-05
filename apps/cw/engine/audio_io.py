"""Loading recordings and finding the CW tone in them.

`load_audio` reads any recording the station can decode: PCM WAV via the
stdlib (no dependencies), everything else (MP3, FLAC, OGG…) via `soundfile`
(libsndfile). `detect_tone` finds the CW note in a recording so operators
don't have to guess the sidetone pitch of an off-air file.
"""
from __future__ import annotations

import io
import wave
from typing import BinaryIO

import numpy as np

from .manager import FloatArray
from .wav import float32_from_wav


def load_audio(path_or_stream: str | BinaryIO) -> tuple[FloatArray, int]:
    """Decode an audio file into (float32 mono samples, sample_rate).

    Tries stdlib WAV first, then falls back to `soundfile` for compressed
    formats (MP3/FLAC/OGG). Raises ValueError with an operator-readable
    message when the file isn't decodable audio.
    """
    if isinstance(path_or_stream, str):
        with open(path_or_stream, "rb") as f:
            return load_audio(f)

    start = path_or_stream.tell()
    try:
        return float32_from_wav(path_or_stream)
    except (wave.Error, EOFError, ValueError):
        path_or_stream.seek(start)

    try:
        import soundfile as sf
    except ImportError as e:  # pragma: no cover - soundfile is a project dep
        raise ValueError(
            "Not a PCM WAV file, and the 'soundfile' package (needed for "
            "MP3/FLAC/OGG) is not installed."
        ) from e

    try:
        data, fs = sf.read(io.BytesIO(path_or_stream.read()), dtype="float32")
    except Exception as e:
        raise ValueError(
            "Couldn't read this file as audio (supported: WAV, MP3, FLAC, OGG)."
        ) from e
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), int(fs)


def detect_tone(
    samples: FloatArray,
    sample_rate: int,
    lo_hz: float = 300.0,
    hi_hz: float = 1200.0,
    window_s: float = 30.0,
) -> float:
    """Estimate the CW tone frequency: the spectral peak in the audio band
    where CW sidetones live. Uses the loudest `window_s` slice so long lead-in
    silence doesn't dilute the spectrum."""
    n_win = min(len(samples), int(window_s * sample_rate))
    if n_win < 256:
        return 600.0
    # pick the loudest window (coarse: compare RMS over thirds of the file)
    best = samples[:n_win]
    best_rms = float(np.sqrt(np.mean(best**2)))
    for start in range(n_win, max(len(samples) - n_win, 1), n_win):
        seg = samples[start : start + n_win]
        rms = float(np.sqrt(np.mean(seg**2)))
        if rms > best_rms:
            best, best_rms = seg, rms
    spectrum = np.abs(np.fft.rfft(best))
    freqs = np.fft.rfftfreq(len(best), 1.0 / sample_rate)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    if not band.any() or float(spectrum[band].max()) <= 0.0:
        return 600.0
    return float(freqs[band][np.argmax(spectrum[band])])
