"""Logbook services — smart QSO creation, band mapping, ADIF export.

The "smart" parts: a quick-logged call links to the session it was heard in,
inherits the session's mode (fldigi:BPSK31 → PSK31), takes the rig's RF when
one is connected, and prefills operator details from your own history with
that station (or QRZ, when credentials exist)."""
from __future__ import annotations

import datetime
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import QuerySet

from .models import QSO, CWSession

# (lo_mhz, hi_mhz, ADIF band name) — the amateur bands a CW/PSK station lives on
BANDS: tuple[tuple[float, float, str], ...] = (
    (1.8, 2.0, "160m"),
    (3.5, 4.0, "80m"),
    (5.3, 5.41, "60m"),
    (7.0, 7.3, "40m"),
    (10.1, 10.15, "30m"),
    (14.0, 14.35, "20m"),
    (18.068, 18.168, "17m"),
    (21.0, 21.45, "15m"),
    (24.89, 24.99, "12m"),
    (28.0, 29.7, "10m"),
    (50.0, 54.0, "6m"),
    (144.0, 148.0, "2m"),
    (420.0, 450.0, "70cm"),
)


def band_for_freq(freq_mhz: float | None) -> str:
    if not freq_mhz:
        return ""
    for lo, hi, name in BANDS:
        if lo <= freq_mhz <= hi:
            return name
    return ""


def mode_for_session(session: CWSession | None) -> str:
    """The session's telemetry knows which engine decoded it."""
    if session is None:
        return "CW"
    engine = str(((session.telemetry or {}).get("meta") or {}).get("engine", "cw"))
    if engine.startswith("fldigi:"):
        modem = engine.split(":", 1)[1].upper()
        if "PSK" in modem:
            return "PSK31" if "31" in modem else "PSK"
        if "RTTY" in modem:
            return "RTTY"
        return modem[:12]
    return "CW"


def worked_before(user: AbstractBaseUser, call: str) -> QuerySet[QSO]:
    return QSO.objects.filter(user=user, call=call.upper())


def quick_log(
    user: AbstractBaseUser,
    call: str,
    session: CWSession | None = None,
    freq_hz: float | None = None,
    source: str = "session",
    rst_sent: str = "599",
) -> QSO:
    """Create a QSO with everything the station already knows filled in."""
    call = call.strip().upper()
    freq_mhz = round(freq_hz / 1e6, 4) if freq_hz else None
    qso = QSO(
        user=user,
        call=call,
        when=session.created_at if session else None,
        freq_mhz=freq_mhz,
        band=band_for_freq(freq_mhz),
        mode=mode_for_session(session),
        rst_sent=rst_sent,
        session=session,
        source=source,
    )
    if qso.when is None:
        from django.utils import timezone

        qso.when = timezone.now()

    # inherit operator details from the last time this station was worked
    previous = worked_before(user, call).first()
    if previous is not None:
        qso.name = previous.name
        qso.qth = previous.qth
        qso.gridsquare = previous.gridsquare
        qso.country = previous.country

    # ...or from QRZ, when the operator has XML-API credentials
    if not qso.name:
        from .qrz import lookup_for_user

        info = lookup_for_user(user, call)
        if info:
            qso.name = info.get("name", "")[:120]
            qso.qth = info.get("qth", "")[:120]
            qso.gridsquare = info.get("grid", "")[:8]
            qso.country = info.get("country", "")[:64]

    qso.save()
    return qso


# ── ADIF ──────────────────────────────────────────────────────────────────
def _adif_field(name: str, value: str) -> str:
    value = str(value)
    return f"<{name}:{len(value)}>{value}"


def adif_export(qsos: QuerySet[QSO] | list[QSO], station_call: str = "") -> str:
    """Serialize QSOs as an ADIF 3 log. Dates/times in UTC per the spec."""
    lines = [
        "CW Station log export",
        _adif_field("adif_ver", "3.1.4"),
        _adif_field("programid", "CW Station"),
        "<EOH>",
        "",
    ]
    for q in qsos:
        when_utc = q.when.astimezone(datetime.timezone.utc)
        fields: list[tuple[str, Any]] = [
            ("call", q.call),
            ("qso_date", when_utc.strftime("%Y%m%d")),
            ("time_on", when_utc.strftime("%H%M%S")),
            ("mode", q.mode),
        ]
        if q.band:
            fields.append(("band", q.band))
        if q.freq_mhz:
            fields.append(("freq", f"{q.freq_mhz:.4f}"))
        if q.rst_sent:
            fields.append(("rst_sent", q.rst_sent))
        if q.rst_rcvd:
            fields.append(("rst_rcvd", q.rst_rcvd))
        if q.name:
            fields.append(("name", q.name))
        if q.qth:
            fields.append(("qth", q.qth))
        if q.gridsquare:
            fields.append(("gridsquare", q.gridsquare))
        if q.country:
            fields.append(("country", q.country))
        if station_call:
            fields.append(("station_callsign", station_call))
        if q.comment:
            fields.append(("comment", " ".join(q.comment.split())))
        lines.append(" ".join(_adif_field(n, v) for n, v in fields) + " <EOR>")
    return "\n".join(lines) + "\n"
