"""Incremental streaming of a live DecodeResult.

`ResultStreamer` watches a decoder's accumulating `DecodeResult` and emits
compact JSON-ready batches of whatever is new — characters, key runs,
envelope points, current WPM/SNR — via an injected `send` callable. The
transport (HTTP POST to the ingest endpoint, a test list, anything) is the
caller's business; this module stays Django-free and unit-testable.
"""
from __future__ import annotations

from typing import Any, Callable

from .bridge import extract_callsigns
from .events import DecodeResult

Sender = Callable[[dict[str, Any]], None]


class ResultStreamer:
    def __init__(
        self, result: DecodeResult, send: Sender, interval_s: float = 0.25
    ) -> None:
        self.result = result
        self.send = send
        self.interval_s = interval_s
        self._chars = 0
        self._runs = 0
        self._env = 0
        self._last_flush = 0.0

    def tick(self, force: bool = False) -> None:
        """Emit a batch if the flush interval elapsed (or `force`)."""
        r = self.result
        now = r.envelope_t[-1] if r.envelope_t else 0.0
        if not force and (now - self._last_flush) < self.interval_s:
            return
        batch = self._diff()
        if batch is not None:
            self.send(batch)
        self._last_flush = now

    def flush(self) -> None:
        """Emit whatever remains (end of stream)."""
        self.tick(force=True)

    def _diff(self) -> dict[str, Any] | None:
        r = self.result
        chars = r.chars[self._chars :]
        runs = r.key_runs[self._runs :]
        env_t = r.envelope_t[self._env :]
        env_mag = r.envelope_mag[self._env :]
        if not (chars or runs or env_t):
            return None
        self._chars = len(r.chars)
        self._runs = len(r.key_runs)
        self._env = len(r.envelope_t)
        last_char = next((c for c in reversed(r.chars) if c.char != " "), None)
        return {
            "chars": [
                {
                    "c": c.char, "m": c.morse, "t0": c.t_start, "t1": c.t_end,
                    "wpm": c.wpm, "snr": c.snr_db, "conf": c.confidence,
                }
                for c in chars
            ],
            "key_runs": [{"on": k.on, "t": k.t_start, "ms": k.dur_ms} for k in runs],
            "env_t": env_t,
            "env_mag": env_mag,
            "meta": {
                "tone_hz": r.tone_hz,
                "wpm": last_char.wpm if last_char else 0.0,
                "snr": last_char.snr_db if last_char else 0.0,
                # every callsign heard so far — the live pages render these as
                # "reply" chips (the responder). Only completed words are
                # scanned: a station mid-word ("...DE W1A") must not spawn a
                # spurious chip before its last character lands.
                "calls": extract_callsigns(
                    r.text if r.text.endswith(" ") else r.text.rsplit(" ", 1)[0]
                ),
            },
        }
