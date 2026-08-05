"""Turn a DecodeResult into a compact 'session' dict the live view animates.

The view is deliberately a *renderer* of what the Python decoder produced, so
there's a single source of truth for the decode — no re-implementing the DSP in
JavaScript.
"""
from __future__ import annotations

import json
from typing import Any

from .events import DecodeResult


def session_from_result(
    result: DecodeResult, truth: str = "", max_env_points: int = 6000
) -> dict[str, Any]:
    # Long recordings produce block-rate envelope traces (250 pts/s — a
    # 7-minute file is >100k points). Decimate to a stored-size cap; the
    # tape only needs visual resolution, chars/key_runs stay exact.
    env_t, env_mag, env_thr = result.envelope_t, result.envelope_mag, result.envelope_thr
    if max_env_points and len(env_t) > max_env_points:
        stride = -(-len(env_t) // max_env_points)  # ceil division
        env_t = env_t[::stride]
        env_mag = env_mag[::stride]
        env_thr = env_thr[::stride]
    return {
        "meta": {
            "engine": result.engine,
            "sample_rate": result.sample_rate,
            "tone_hz": result.tone_hz,
            "wpm_final": result.wpm_final,
            "truth": truth.upper(),
            "decoded": result.text,
        },
        # envelope trace (already normalized 0..1), decimated for storage
        "env_t": env_t,
        "env_mag": env_mag,
        # keyed runs for the "paper tape" view
        "key_runs": [{"on": r.on, "t": r.t_start, "ms": r.dur_ms} for r in result.key_runs],
        # decoded characters on a timeline
        "chars": [
            {
                "c": c.char, "m": c.morse, "t0": c.t_start, "t1": c.t_end,
                "wpm": c.wpm, "snr": c.snr_db, "conf": c.confidence,
            }
            for c in result.chars
        ],
    }


def sessions_to_js(sessions: dict[str, dict[str, Any]]) -> str:
    """Serialize a name->session map as a JS assignment for embedding."""
    return "const SESSIONS = " + json.dumps(sessions, separators=(",", ":")) + ";"
