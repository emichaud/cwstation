"""CW / ham-radio shorthand → plain English, for tutor mode.

The abbreviations, Q-codes, and prosigns an operator hears constantly on the
air. This is the single source of truth: tutor mode on the live tape reads
it (served at /cw/abbrev/), the Morse reference appendix lists it, and
`gloss()` is unit-tested.
"""
from __future__ import annotations

# Grouped for the appendix; flattened into one lookup for glossing.
ABBREVIATIONS: dict[str, dict[str, str]] = {
    "Q-codes": {
        "QRL": "is this frequency in use? / I'm busy",
        "QRM": "interference (from other stations)",
        "QRN": "static / natural noise",
        "QRO": "increase power",
        "QRP": "low power operating",
        "QRQ": "send faster",
        "QRS": "send more slowly",
        "QRT": "stop sending / going off the air",
        "QRV": "ready / standing by",
        "QRX": "wait / stand by",
        "QRZ": "who is calling me?",
        "QSB": "your signal is fading",
        "QSL": "acknowledged / I confirm",
        "QSO": "a contact / conversation",
        "QSY": "change frequency",
        "QTH": "location / station address",
        "QTR": "the time",
    },
    "Calling & procedure": {
        "CQ": "calling any station",
        "DE": "from / this is",
        "K": "over — go ahead, anyone",
        "KN": "over — named station only",
        "AR": "end of message",
        "SK": "end of contact — signing off",
        "BK": "break — quick back-and-forth",
        "BT": "separator / new paragraph",
        "AS": "wait / stand by",
        "CT": "start of message",
        "R": "roger — received OK",
        "CL": "closing station",
    },
    "Prosigns — as they appear in copy": {
        # the decoder renders run-together prosigns as these signs/brackets,
        # so tutor mode must gloss the forms actually seen on the tape
        "+": "end of message (AR)",
        "=": "separator (BT) — new thought",
        "(": "go ahead — named station only (KN)",
        "&": "wait / stand by (AS)",
        "<SK>": "end of contact — signing off",
        "<BK>": "break-in — over to you",
        "<CT>": "start of message (KA)",
    },
    "Common words": {
        "ABT": "about",
        "AGN": "again",
        "ANT": "antenna",
        "BCNU": "be seeing you",
        "BTU": "back to you",
        "CFM": "confirm",
        "CPY": "copy",
        "CUL": "see you later",
        "CUAGN": "see you again",
        "DR": "dear",
        "DX": "distant / long-distance station",
        "ES": "and",
        "FB": "fine business — excellent",
        "FER": "for",
        "FM": "from",
        "GA": "good afternoon",
        "GB": "goodbye",
        "GE": "good evening",
        "GL": "good luck",
        "GM": "good morning",
        "GN": "good night",
        "GUD": "good",
        "HI": "laughter (ham 'ha ha')",
        "HR": "here",
        "HW": "how do you copy?",
        "MNI": "many",
        "NIL": "nothing / I have nothing for you",
        "NR": "number",
        "NW": "now",
        "OM": "old man — any operator",
        "OP": "operator / name",
        "PSE": "please",
        "PWR": "power",
        "RIG": "radio equipment",
        "RST": "signal report (readability-strength-tone)",
        "SRI": "sorry",
        "TKS": "thanks",
        "TNX": "thanks",
        "TU": "thank you",
        "UR": "your / you are",
        "VY": "very",
        "WID": "with",
        "WKD": "worked",
        "WL": "well / will",
        "WUD": "would",
        "WX": "weather",
        "XYL": "wife",
        "YL": "young lady — female operator",
    },
    "Numbers & signs": {
        "73": "best regards",
        "88": "love and kisses",
        "5NN": "599 — a perfect report",
    },
}

# Signs that carry meaning on their own — passed through the tokenizer intact
# instead of being stripped as trailing punctuation.
SIGN_TOKENS = frozenset({"+", "=", "(", ")", "&"})

# token → meaning, everything flattened
LOOKUP: dict[str, str] = {
    token: meaning
    for group in ABBREVIATIONS.values()
    for token, meaning in group.items()
}


def _clean(token: str) -> str:
    """Normalize a copied word for lookup: standalone signs pass through,
    prosign brackets are preserved, otherwise strip surrounding punctuation
    and uppercase."""
    if token in SIGN_TOKENS:
        return token
    return token.strip(".,?!;:/").upper()  # keep <>, () — they carry meaning


def gloss(text: str) -> list[dict[str, str]]:
    """Every known abbreviation in `text`, in first-appearance order,
    de-duplicated. Returns [{'token': 'CQ', 'meaning': '...'}, ...]."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for raw in text.split():
        token = _clean(raw)
        if token and token in LOOKUP and token not in seen:
            seen.add(token)
            out.append({"token": token, "meaning": LOOKUP[token]})
    return out
