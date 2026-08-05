"""Glue between the Django-free engine and the web app.

Each function runs one engine pass and persists a `CWSession` carrying the
full replay telemetry. This is the only module that imports both sides.
"""
from __future__ import annotations

from typing import BinaryIO

from django.contrib.auth.base_user import AbstractBaseUser

from .engine import CWConfig, decode_array, detect_tone, load_audio, synthesize_cw  # noqa: F401
from .engine.bridge import extract_callsigns
from .engine.events import DecodeResult  # noqa: F401 - used in annotations
from .engine.export import session_from_result
from .engine.wav import wav_bytes_from_float32
from .models import CWSession


def decode_practice(
    user: AbstractBaseUser,
    text: str,
    wpm: float,
    tone_hz: float,
    snr_db: float | None,
) -> CWSession:
    """Synthesize `text` at the given speed/tone/noise and decode it back."""
    synth = synthesize_cw(text, wpm=wpm, tone_hz=tone_hz, sample_rate=8000, snr_db=snr_db)
    result = decode_array(synth.audio, synth.sample_rate, CWConfig(tone_hz=tone_hz))
    return CWSession.objects.create(
        user=user,
        direction=CWSession.Direction.RECEIVED,
        source=CWSession.Source.SYNTH,
        text=result.text,
        truth=synth.text,
        wpm=wpm,  # requested speed, so audio regenerates exactly; decoded speed lives in telemetry
        tone_hz=tone_hz,
        snr_db=snr_db,
        callsigns=extract_callsigns(result.text),
        telemetry=session_from_result(result, truth=synth.text),
    )


def decode_recording(
    user: AbstractBaseUser, stream: BinaryIO, tone_hz: float | None
) -> CWSession:
    """Decode an uploaded recording (WAV/MP3/FLAC/OGG) recorded off a receiver.

    With `tone_hz=None` the CW note is auto-detected from the spectrum — the
    right default for off-air files where the operator doesn't know the pitch.
    """
    audio, sample_rate = load_audio(stream)
    if tone_hz is None:
        tone_hz = detect_tone(audio, sample_rate)
    result = decode_array(audio, sample_rate, CWConfig(tone_hz=tone_hz))
    return CWSession.objects.create(
        user=user,
        direction=CWSession.Direction.RECEIVED,
        source=CWSession.Source.WAV,
        text=result.text,
        wpm=result.wpm_final,
        tone_hz=tone_hz,
        callsigns=extract_callsigns(result.text),
        telemetry=session_from_result(result),
    )


def compose_send(user: AbstractBaseUser, text: str, wpm: float, tone_hz: float) -> CWSession:
    """Key `text` into CW audio. The session self-decodes the generated audio
    so the monitor can replay exactly what will go out on the air."""
    synth = synthesize_cw(text, wpm=wpm, tone_hz=tone_hz, sample_rate=8000)
    result = decode_array(synth.audio, synth.sample_rate, CWConfig(tone_hz=tone_hz))
    return CWSession.objects.create(
        user=user,
        direction=CWSession.Direction.SENT,
        source=CWSession.Source.TEXT,
        text=synth.text,
        wpm=wpm,
        tone_hz=tone_hz,
        callsigns=extract_callsigns(synth.text),
        telemetry=session_from_result(result, truth=synth.text),
    )


def save_live_session(
    user: AbstractBaseUser, result: "DecodeResult", tone_hz: float
) -> CWSession:
    """Persist a live-monitor run (audio itself is not stored)."""
    return CWSession.objects.create(
        user=user,
        direction=CWSession.Direction.RECEIVED,
        source=CWSession.Source.LIVE,
        text=result.text,
        wpm=result.wpm_final,
        tone_hz=tone_hz,
        callsigns=extract_callsigns(result.text),
        telemetry=session_from_result(result),
    )


def apply_receiver_controls(
    user: AbstractBaseUser,
    cfg: "CWConfig",
    source: object | None = None,
) -> None:
    """Pull the operator's knob values (Simulator/Live page sliders) and apply
    them to a running decoder — and, when given, a simulated band source.

    Called by `cw_monitor_live` and `cw_simulate` between audio blocks; the DB
    row is the cross-process control channel.
    """
    from .models import CWSimControl

    control = CWSimControl.objects.filter(user=user).first()
    if control is None:
        return
    control.clamped()
    cfg.input_gain = control.input_gain
    cfg.squelch_db = control.squelch_db
    cfg.afc = control.afc
    if source is not None:
        source.noise_level = control.noise_level  # type: ignore[attr-defined]
        source.paused_signals = control.paused_signals  # type: ignore[attr-defined]


def session_wav_bytes(session: CWSession) -> bytes:
    """Regenerate the session's audio as WAV bytes.

    Deterministic for synthesized sessions (`has_audio`); raises for WAV
    uploads, whose audio was never stored.
    """
    if not session.has_audio:
        raise ValueError("Audio for uploaded-WAV sessions is not stored.")
    source_text = session.truth or session.text
    synth = synthesize_cw(
        source_text, wpm=session.wpm or 20.0, tone_hz=session.tone_hz, sample_rate=8000,
        snr_db=session.snr_db,
    )
    return wav_bytes_from_float32(synth.audio, synth.sample_rate)
