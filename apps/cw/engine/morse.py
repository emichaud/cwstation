"""International Morse code tables and element<->text mapping.

Kept deliberately free of any DSP so it can be unit-tested and reused by both
the decoder (elements -> text) and the synthesizer (text -> elements).
"""
from __future__ import annotations

UNKNOWN_CHAR = "�"

# Character -> dot/dash string. International Morse plus the punctuation and
# prosigns you actually hit on the air.
CHAR_TO_MORSE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...",
    ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-",
    '"': ".-..-.", "@": ".--.-.",
    # Prosigns (sent as a single run-together symbol, shown with < >)
    "<AR>": ".-.-.", "<SK>": "...-.-", "<BT>": "-...-", "<KN>": "-.--.",
    "<BK>": "-...-.-", "<AS>": ".-...", "<CT>": "-.-.-", "<KA>": "-.-.-",
}

# Reverse map for decoding. Where a code collides with a prosign (e.g. "+"
# and <AR> are both .-.-.), the plain character wins for readability.
MORSE_TO_CHAR: dict[str, str] = {}
for _ch, _code in CHAR_TO_MORSE.items():
    if _code not in MORSE_TO_CHAR or not _ch.startswith("<"):
        MORSE_TO_CHAR[_code] = _ch


def decode_symbol(code: str) -> str:
    """Map a single dot/dash string to a character, or UNKNOWN_CHAR if unknown."""
    return MORSE_TO_CHAR.get(code, UNKNOWN_CHAR)


def encode_text(text: str) -> list[str]:
    """Text -> list of dot/dash symbols (one per character; " " marks a word
    gap, which callers turn into timing). Unknown characters are skipped.
    Prosigns may be written inline as `<AR>` etc. and encode as one symbol."""
    out: list[str] = []
    i = 0
    up = text.upper()
    while i < len(up):
        ch = up[i]
        if ch == " ":
            out.append(" ")
            i += 1
            continue
        if ch == "<":
            end = up.find(">", i)
            if end != -1 and up[i : end + 1] in CHAR_TO_MORSE:
                out.append(CHAR_TO_MORSE[up[i : end + 1]])
                i = end + 1
                continue
        if ch in CHAR_TO_MORSE:
            out.append(CHAR_TO_MORSE[ch])
        i += 1
    return out
