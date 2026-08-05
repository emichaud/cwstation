"""Put a keyed message on the air through the operator's rig.

The sequence — the part that makes this *control* rather than hope:

    CAT PTT on → wait ptt_lead_ms → play the session's audio out the sound
    device into the rig → PTT off (in a finally, always).

One transmission at a time per process; state is queryable for the UI. The
audio player is injected so tests exercise the full PTT sequence against a
fake rigctld with no sound hardware.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .engine.manager import FloatArray
from .models import CWRig, CWSession
from .rigctl import RigctldClient, RigError

Player = Callable[[FloatArray, int, str], None]

_lock = threading.Lock()
_state: dict[str, Any] = {"transmitting": False, "session_id": None, "error": ""}


def _default_player(audio: FloatArray, sample_rate: int, device: str) -> None:
    """Blocking playback via sounddevice (the optional live-audio extra)."""
    try:
        import sounddevice as sd
    except ImportError as e:  # pragma: no cover - dep hint
        raise RigError(
            "TX audio needs the 'sounddevice' package: uv sync --extra live"
        ) from e
    dev: str | int | None = device or None
    if isinstance(dev, str) and dev.isdigit():
        dev = int(dev)
    sd.play(audio, sample_rate, device=dev)
    sd.wait()


def tx_state() -> dict[str, Any]:
    return dict(_state)


def transmit_session(
    rig: CWRig, session: CWSession, player: Player | None = None, blocking: bool = False
) -> dict[str, Any]:
    """Key `session` through `rig`. Returns the TX state immediately (the
    transmission runs in a worker thread unless `blocking`)."""
    from .services import session_audio_float

    if not session.has_audio:
        raise RigError("This session's audio can't be regenerated (uploaded recording).")
    if not _lock.acquire(blocking=False):
        raise RigError("Already transmitting — one message at a time.")

    audio, sample_rate = session_audio_float(session)
    play = player or _default_player

    def run() -> None:
        _state.update(transmitting=True, session_id=session.pk, error="")
        client = RigctldClient(rig.host, rig.port)
        try:
            client.connect()
            if rig.use_ptt:
                if client.get_ptt():
                    raise RigError("PTT is already keyed — not stomping on it.")
                client.set_ptt(True)
                time.sleep(rig.ptt_lead_ms / 1000.0)
            try:
                play(audio, sample_rate, rig.audio_output)
            finally:
                if rig.use_ptt:
                    client.set_ptt(False)  # ALWAYS unkey, even if playback died
        except RigError as e:
            _state["error"] = str(e)
        except Exception as e:  # playback device errors etc.
            _state["error"] = f"TX failed: {e}"
        finally:
            client.close()
            _state.update(transmitting=False, session_id=None)
            _lock.release()

    if blocking:
        run()
    else:
        threading.Thread(target=run, name=f"cw-tx-{session.pk}", daemon=True).start()
    return tx_state()
