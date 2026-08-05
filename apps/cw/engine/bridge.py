"""Bridge the audio layer toward a future logbook.

`CWLogBridge` subscribes to the AudioEngineManager and turns the decoded text
stream into a live QSO draft: it watches for callsigns and RST reports as they
come off the air and accumulates the raw copy. It's deliberately framework-
agnostic — `on_qso_ready` is the single hook the Django side overrides to
persist a QSO (or push it to the operator over WebSocket). Nothing here imports
Django, so it stays unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .events import CharEvent

# Rough amateur callsign shape: 1-2 char prefix, a digit, 1-4 char suffix.
CALLSIGN_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z]{1,4})\b")
RST_RE = re.compile(r"\b([1-5][1-9][1-9])\b")


@dataclass
class QSODraft:
    raw: str = ""  # everything copied
    callsigns: list[str] = field(default_factory=list)
    rst: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.strip(),
            "call": self.callsigns[-1] if self.callsigns else "",
            "rst": self.rst[-1] if self.rst else "",
            "callsigns_seen": self.callsigns,
            "rst_seen": self.rst,
        }


def extract_callsigns(text: str) -> list[str]:
    """All callsign-shaped words in decoded copy, in order, deduplicated."""
    seen: list[str] = []
    for m in CALLSIGN_RE.findall(text.upper()):
        if m not in seen:
            seen.append(m)
    return seen


class CWLogBridge:
    def __init__(self, gap_close_words: int = 3) -> None:
        self.draft = QSODraft()
        self._word = ""
        self._space_run = 0
        self._gap_close = gap_close_words

    def on_char(self, ev: CharEvent) -> None:
        """Subscribe this to AudioEngineManager.subscribe(...)."""
        self.draft.raw += ev.char
        if ev.char == " ":
            self._flush_word()
            self._space_run += 1
            if self._space_run >= self._gap_close and self.draft.raw.strip():
                self.on_qso_ready(self.draft)
        else:
            self._space_run = 0
            self._word += ev.char

    def _flush_word(self) -> None:
        w = self._word.strip()
        self._word = ""
        if not w:
            return
        if CALLSIGN_RE.fullmatch(w):
            self.draft.callsigns.append(w)
        elif RST_RE.fullmatch(w):
            self.draft.rst.append(w)

    # -- override me ---------------------------------------------------------
    def on_qso_ready(self, draft: QSODraft) -> None:
        """Called when copy has gone quiet long enough to look like a finished
        exchange. The Django side overrides this to create a QSO row / notify
        the operator. Default: no-op."""
