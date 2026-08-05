"""Turn a DecodeResult into a compact 'session' dict the modern view animates.

The view is deliberately a *renderer* of what the Python decoder produced, so
there's a single source of truth for the decode — no re-implementing the DSP in
JavaScript.
"""
from __future__ import annotations
import json

from .events import DecodeResult


def session_from_result(result: DecodeResult, truth: str = "") -> dict:
    return {
        "meta": {
            "engine": result.engine,
            "sample_rate": result.sample_rate,
            "tone_hz": result.tone_hz,
            "wpm_final": result.wpm_final,
            "truth": truth.upper(),
            "decoded": result.text,
        },
        # envelope trace (already normalized 0..1) sampled at block rate
        "env_t": result.envelope_t,
        "env_mag": result.envelope_mag,
        # keyed runs for the "paper tape" view
        "key_runs": [{"on": r.on, "t": r.t_start, "ms": r.dur_ms} for r in result.key_runs],
        # decoded characters on a timeline
        "chars": [
            {"c": c.char, "m": c.morse, "t0": c.t_start, "t1": c.t_end,
             "wpm": c.wpm, "snr": c.snr_db, "conf": c.confidence}
            for c in result.chars
        ],
    }


def sessions_to_js(sessions: dict[str, dict]) -> str:
    """Serialize a name->session map as a JS assignment for embedding."""
    return "const SESSIONS = " + json.dumps(sessions, separators=(",", ":")) + ";"
