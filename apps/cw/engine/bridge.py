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
DE_CALL_RE = re.compile(r"\bDE\s+([A-Z]{1,2}\d[A-Z]{1,4})\b")

# The shape test alone is too generous on noisy copy: band noise that survives
# the squelch spells short tokens like "O1E", which matches the pattern and then
# reaches the operator as a one-click "+log" chip. Two cheap, offline checks cut
# that without a callsign database.
#
# 1. Prefix plausibility. ITU allocates most prefix blocks two characters wide
#    (O is only ever OA-OZ, S only SA-SZ, and so on). Only these letters are
#    allocated as *standalone* single-character prefixes, so "O1E" is not a
#    callsign anyone can hold, while W1AW and N0CALL are fine. Two-character
#    prefixes are accepted as-is — validating those needs the full ITU table and
#    wrongly dropping real DX is worse than the odd false positive.
# 2. Corroboration for 3-character tokens. Those are the ones noise produces; a
#    real short call still passes if it is sent the way operators actually send
#    a call — after "DE", or repeated.
#
# Note: character *confidence* is useless here. A clean-sounding noise burst
# decodes at confidence 1.0, identical to a real signal (measured on the off-air
# fixtures), so it cannot separate O1E from W1AW.
SINGLE_LETTER_PREFIXES = frozenset("BFGIKMNRW")
MIN_UNCORROBORATED_LEN = 4


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


def has_plausible_prefix(call: str) -> bool:
    """False for callsign-shaped tokens whose prefix no country is allocated —
    the common shape of a callsign invented by band noise."""
    prefix = call[: call.index(next(c for c in call if c.isdigit()))]
    return len(prefix) > 1 or prefix in SINGLE_LETTER_PREFIXES


def extract_callsigns(text: str) -> list[str]:
    """Callsigns in decoded copy, in order, deduplicated.

    Filters tokens that match the shape but can't be real (see the notes on
    `SINGLE_LETTER_PREFIXES`), so noise-born junk never reaches the operator as
    a loggable chip.
    """
    upper = text.upper()
    tokens = CALLSIGN_RE.findall(upper)
    after_de = set(DE_CALL_RE.findall(upper))
    seen: list[str] = []
    for m in tokens:
        if m in seen or not has_plausible_prefix(m):
            continue
        corroborated = m in after_de or tokens.count(m) > 1
        if len(m) < MIN_UNCORROBORATED_LEN and not corroborated:
            continue
        seen.append(m)
    return seen


class CWLogBridge:
    def __init__(self, gap_close_words: int = 3) -> None:
        self.draft = QSODraft()
        self._word = ""
        self._space_run = 0
        self._gap_close = gap_close_words
        self._prev_word = ""  # so a call sent as "DE <call>" is corroborated

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
        prev, self._prev_word = self._prev_word, w
        if CALLSIGN_RE.fullmatch(w):
            # Same admission rule as extract_callsigns, applied to the streaming
            # word feed: corroboration here is "followed DE" or "heard before".
            corroborated = prev == "DE" or w in self.draft.callsigns
            if has_plausible_prefix(w) and (
                len(w) >= MIN_UNCORROBORATED_LEN or corroborated
            ):
                self.draft.callsigns.append(w)
        elif RST_RE.fullmatch(w):
            self.draft.rst.append(w)

    # -- override me ---------------------------------------------------------
    def on_qso_ready(self, draft: QSODraft) -> None:
        """Called when copy has gone quiet long enough to look like a finished
        exchange. The Django side overrides this to create a QSO row / notify
        the operator. Default: no-op."""
