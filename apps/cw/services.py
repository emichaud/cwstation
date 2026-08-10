"""Glue between the Django-free engine and the web app.

Each function runs one engine pass and persists a `CWSession` carrying the
full replay telemetry. This is the only module that imports both sides.
"""
from __future__ import annotations

from typing import BinaryIO

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser

from .engine import CWConfig, decode_array, detect_tone, load_audio, synthesize_cw  # noqa: F401
from .engine.bridge import extract_callsigns
from .engine.events import DecodeResult  # noqa: F401 - used in annotations
from .engine.export import session_from_result
from .engine.wav import wav_bytes_from_float32
from .models import CWSession

# Squelch default for off-air recordings, in dB of estimated SNR.
#
# Chosen by sweeping real material rather than by taste. Against a weak 40 m
# QSO recorded off a WebSDR and a noise-only stretch of empty band:
#
#     gate     noise-only chars     weak-signal copy
#     0.0 dB          411           intact (baseline)
#     4.5 dB           65           intact
#     5.0 dB           19           starts losing words
#     6.0 dB            5           badly degraded
#
# 4.5 dB is the knee: it drops ~84% of the noise hash while every word of the
# real (weak) QSO still copies. Above it, real signal goes before noise does.
# Operators can override per upload — the Decode page exposes the slider, and
# 0 restores the old ungated behaviour.
RECORDING_SQUELCH_DB = 4.5

# Last-resort stream target. Only reached when no SITE_URL-style setting is
# configured; development.py sets one from PORT so this rarely applies.
FALLBACK_STREAM_SERVER = "http://127.0.0.1:8005"


def default_stream_server() -> str:
    """Base URL the streaming commands POST live batches back to.

    Resolved from settings rather than hardcoded per command, so a server on a
    non-default port needs one change, not one per command. Same name order
    `apps/webhooks/context.py` uses for the same question. `--server` still wins.
    """
    for name in ("SITE_URL", "SMALLSTACK_SITE_URL", "BASE_URL"):
        value = str(getattr(settings, name, "") or "").strip()
        if value.startswith(("http://", "https://")):
            return value.rstrip("/")
    return FALLBACK_STREAM_SERVER


def station_callsign(user: AbstractBaseUser) -> str:
    """The operator's station callsign, upper-cased: their configured CWRig
    callsign if set, otherwise their username. Feeds {mycall} in send macros
    and the ADIF STATION_CALLSIGN so both agree."""
    from .models import CWRig

    call = ""
    rig = CWRig.objects.filter(user=user).only("callsign").first()
    if rig and rig.callsign:
        call = rig.callsign
    return (call or getattr(user, "username", "") or "").upper()


def station_defaults(user: AbstractBaseUser) -> dict:
    """Everything a page needs to seed a keyer: the resolved callsign and the
    operator's default WPM + sidetone. The send popup and the decode keyer both
    start from these (and let the operator override per message)."""
    from .models import CWRig

    rig = CWRig.objects.filter(user=user).only("callsign", "send_wpm", "send_tone_hz").first()
    return {
        "call": station_callsign(user),
        "wpm": rig.send_wpm if rig else 20,
        "tone_hz": rig.send_tone_hz if rig else 600,
    }


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
    user: AbstractBaseUser,
    stream: BinaryIO,
    tone_hz: float | None,
    squelch_db: float = RECORDING_SQUELCH_DB,
) -> CWSession:
    """Decode an uploaded recording (WAV/MP3/FLAC/OGG) recorded off a receiver.

    With `tone_hz=None` the CW note is auto-detected from the spectrum — the
    right default for off-air files where the operator doesn't know the pitch.

    Off-air files carry band noise between the marks, so the squelch gate is on
    by default here (unlike the synthesized practice paths, which are clean by
    construction). See `RECORDING_SQUELCH_DB` for how the default was chosen.
    """
    audio, sample_rate = load_audio(stream)
    if tone_hz is None:
        tone_hz = detect_tone(audio, sample_rate)
    result = decode_array(
        audio, sample_rate, CWConfig(tone_hz=tone_hz, squelch_db=squelch_db)
    )
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


def session_audio_float(session: CWSession) -> tuple["object", int]:
    """Regenerate the session's audio as (float32 samples, sample_rate) —
    the transmit path plays this out the sound device into the rig."""
    if not session.has_audio:
        raise ValueError("Audio for uploaded-recording sessions is not stored.")
    source_text = session.truth or session.text
    synth = synthesize_cw(
        source_text, wpm=session.wpm or 20.0, tone_hz=session.tone_hz, sample_rate=8000,
        snr_db=session.snr_db,
    )
    return synth.audio, synth.sample_rate


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
