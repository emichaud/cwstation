"""QRZ.com *logbook* API — import from and export to logbook.qrz.com.

This is a separate service from the XML callsign lookup: QRZ issues an API
key **per logbook** (Logbook → Settings → API), distinct from the account
password. The protocol is form-POST key/value in, `RESULT=OK&...` out, with
ADIF payloads HTML-entity-encoded. Base URL is settings-overridable so
tests run against a fake server.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

DEFAULT_URL = "https://logbook.qrz.com/api"


class QRZLogbookError(Exception):
    """logbook.qrz.com unreachable or the key/request was rejected."""


def _base_url() -> str:
    return getattr(settings, "QRZ_LOGBOOK_URL", DEFAULT_URL)


def _call(params: dict[str, str]) -> str:
    data = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(_base_url(), data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise QRZLogbookError(f"logbook.qrz.com unreachable ({e})") from e


def _field(body: str, name: str) -> str:
    match = re.search(rf"{name}=([^&]*)", body)
    return match.group(1) if match else ""


def fetch_adif(key: str) -> str:
    """Download the whole QRZ logbook as ADIF."""
    body = _call({"KEY": key, "ACTION": "FETCH", "OPTION": "TYPE:ADIF"})
    if _field(body, "RESULT") not in ("OK", "AUTH"):  # AUTH quirk on empty logs
        reason = html.unescape(_field(body, "REASON")) or body[:120]
        raise QRZLogbookError(f"QRZ logbook fetch failed: {reason}")
    match = re.search(r"ADIF=(.*)$", body, re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(1))


def insert_record(key: str, adif_record: str) -> str:
    """Insert one ADIF record. Returns "ok" | "duplicate". Raises on real
    failures (bad key, malformed record)."""
    body = _call({"KEY": key, "ACTION": "INSERT", "ADIF": adif_record})
    result = _field(body, "RESULT")
    if result == "OK":
        return "ok"
    reason = html.unescape(_field(body, "REASON"))
    if result in ("FAIL", "REPLACE") and "duplicate" in reason.lower():
        return "duplicate"
    raise QRZLogbookError(f"QRZ logbook insert failed: {reason or body[:120]}")
