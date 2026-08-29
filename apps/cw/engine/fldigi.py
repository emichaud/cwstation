"""The fldigi tap — PSK31 (and every other fldigi mode) on the same seam.

fldigi is the ham world's standard sound-card digital-modes program; it
exposes received text over XML-RPC (default http://127.0.0.1:7362). This
tap adapts that stream into our `CharEvent` contract — the first real
`NetworkTapEngine`, proving the multi-mode seam: no consumer (tape, live
view, bridge, sessions) changes at all.

stdlib `xmlrpc.client` only; no new dependencies. Timing is wall-clock
relative to tap start (fldigi doesn't timestamp characters); fldigi's
signal-quality metric maps to CharEvent confidence.
"""
from __future__ import annotations

import time
import xmlrpc.client
from typing import Any, Callable

from .events import CharEvent, DecodeResult


class FldigiError(Exception):
    """fldigi unreachable or its XML-RPC call failed."""


class FldigiTap:
    """Polls a running fldigi for decoded text. Register alongside audio
    demodulators — `poll()` returns the same CharEvents they emit."""

    name = "fldigi"

    def __init__(
        self,
        url: str = "http://127.0.0.1:7362",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self._proxy = xmlrpc.client.ServerProxy(url, allow_none=True)
        self._clock = clock
        self._t0: float | None = None
        self.modem = ""
        self.carrier_hz = 0.0

    # -- lifecycle -----------------------------------------------------------
    def _call(self, method: str, *args: Any) -> Any:
        try:
            return getattr(self._proxy, method)(*args)
        except (OSError, xmlrpc.client.Error) as e:
            raise FldigiError(f"fldigi at {self.url}: {method} failed ({e})") from e

    def connect(self) -> dict[str, Any]:
        """Probe fldigi; returns version/modem info. Starts the tap clock."""
        info: dict[str, Any] = {
            "version": str(self._call("fldigi.version")),
            "modem": str(self._call("modem.get_name")),
            "carrier_hz": float(self._call("modem.get_carrier")),
            "dial_hz": float(self._call("main.get_frequency")),
        }
        self.modem = info["modem"]
        self.carrier_hz = info["carrier_hz"]
        self._t0 = self._clock()
        return info

    def reset(self) -> None:
        self._t0 = self._clock()

    # -- the tap -------------------------------------------------------------
    def poll(self) -> list[CharEvent]:
        """New characters fldigi decoded since the last poll."""
        if self._t0 is None:
            self.connect()
        raw = self._call("rx.get_data")
        data = raw.data if isinstance(raw, xmlrpc.client.Binary) else raw
        if isinstance(data, bytes):
            text = data.decode("utf-8", "replace")
        else:
            text = str(data or "")
        if not text:
            return []
        # fldigi separates lines/overs with CR/LF; on the tape those are word gaps
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        try:
            quality = float(self._call("modem.get_quality"))  # 0..100
        except FldigiError:
            quality = 100.0
        try:
            self.carrier_hz = float(self._call("modem.get_carrier"))
        except FldigiError:
            pass

        # connect() above sets _t0; the fallback keeps the type honest
        # without restating that invariant as an assert.
        now = self._clock() - (self._t0 or 0.0)
        confidence = max(0.0, min(quality / 100.0, 1.0))
        events: list[CharEvent] = []
        for ch in text:
            events.append(
                CharEvent(
                    char=ch.upper() if ch != " " else " ",
                    morse="",  # not a keyed mode — no element string
                    t_start=round(now, 4),
                    t_end=round(now, 4),
                    wpm=0.0,
                    snr_db=round(quality, 1),
                    confidence=confidence,
                )
            )
        return events

    def status(self) -> dict[str, Any]:
        return {
            "modem": str(self._call("modem.get_name")),
            "carrier_hz": float(self._call("modem.get_carrier")),
            "dial_hz": float(self._call("main.get_frequency")),
        }


def tap_loop(
    tap: FldigiTap,
    result: DecodeResult,
    on_char: Callable[[CharEvent], None] | None = None,
    on_tick: Callable[[DecodeResult], None] | None = None,
    poll_s: float = 0.25,
    duration_s: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DecodeResult:
    """Drive the tap until Ctrl-C (or `duration_s`), accumulating into
    `result` — the same shape every other engine produces, so streaming and
    session-saving reuse the existing paths unchanged."""
    started = tap._clock()
    try:
        while True:
            for ev in tap.poll():
                result.chars.append(ev)
                result.text += ev.char
                if on_char:
                    on_char(ev)
            result.tone_hz = tap.carrier_hz
            result.engine = "fldigi:" + (tap.modem or "?")
            if on_tick:
                on_tick(result)
            if duration_s is not None and tap._clock() - started >= duration_s:
                break
            sleep(poll_s)
    except KeyboardInterrupt:  # pragma: no cover - live exit path
        pass
    result.text = result.text.strip()
    return result
