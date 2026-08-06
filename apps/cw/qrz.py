"""QRZ.com callbook lookups over the XML API.

Requires a QRZ XML-data subscription; the operator's credentials live in
`QRZProfile`. Session keys are cached and transparently refreshed on
timeout. Everything degrades gracefully: no credentials, bad credentials,
or an unknown call all mean "no enrichment", never an error in the logging
flow. The base URL is swappable so tests run against a fake QRZ server.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from django.conf import settings

DEFAULT_URL = "https://xmldata.qrz.com/xml/current/"


class QRZError(Exception):
    """QRZ unreachable, bad credentials, or malformed response."""


def _base_url() -> str:
    return getattr(settings, "QRZ_XML_URL", DEFAULT_URL)


def _fetch(params: dict[str, str]) -> ET.Element:
    url = _base_url() + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise QRZError(f"QRZ unreachable ({e})") from e
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise QRZError("QRZ returned malformed XML") from e
    # QRZ namespaces its responses; strip so tags are addressable plainly
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def _text(root: ET.Element, path: str) -> str:
    node = root.find(path)
    return (node.text or "").strip() if node is not None else ""


def authenticate(username: str, password: str) -> str:
    """Get a session key. Raises QRZError with QRZ's own message on failure."""
    root = _fetch({"username": username, "password": password, "agent": "cwstation"})
    key = _text(root, "Session/Key")
    if not key:
        raise QRZError(_text(root, "Session/Error") or "QRZ login failed")
    return key


def lookup(session_key: str, call: str) -> dict[str, Any] | None:
    """Look a callsign up. Returns None for not-found; raises QRZError on a
    dead session (caller re-authenticates)."""
    root = _fetch({"s": session_key, "callsign": call})
    error = _text(root, "Session/Error")
    if error:
        if "Session Timeout" in error or "Invalid session key" in error:
            raise QRZError("session expired")
        return None  # "Not found: X" and friends
    if root.find("Callsign") is None:
        return None
    first = _text(root, "Callsign/fname")
    last = _text(root, "Callsign/name")
    return {
        "call": _text(root, "Callsign/call") or call.upper(),
        "name": (first + " " + last).strip(),
        "qth": ", ".join(p for p in (_text(root, "Callsign/addr2"), _text(root, "Callsign/state")) if p),
        "grid": _text(root, "Callsign/grid"),
        "country": _text(root, "Callsign/country"),
    }


def lookup_for_user(user: Any, call: str) -> dict[str, Any] | None:
    """The logging-flow entry point: uses the operator's stored credentials,
    caches the session key, retries once on session timeout, and returns
    None instead of raising — enrichment must never break logging."""
    from .models import QRZProfile

    profile = QRZProfile.objects.filter(user=user).first()
    if profile is None or not profile.username or not profile.password:
        return None
    try:
        if not profile.session_key:
            profile.session_key = authenticate(profile.username, profile.password)
            profile.save(update_fields=["session_key", "updated_at"])
        try:
            return lookup(profile.session_key, call)
        except QRZError:  # stale session — one fresh login, one retry
            profile.session_key = authenticate(profile.username, profile.password)
            profile.save(update_fields=["session_key", "updated_at"])
            return lookup(profile.session_key, call)
    except QRZError:
        return None
