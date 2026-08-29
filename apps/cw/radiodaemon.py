"""Launch and supervise a local `rtl_fm` for broadcast FM listening.

The FM Radio page's engine — the receiver sibling of `rigdaemon.py`: enumerate
the RTL-SDR dongles present, start `rtl_fm` on a chosen frequency, pump its
audio at a sink, keep the recent log lines, and stop it. One managed receiver
per server process; the dongle is exclusive hardware.

Everything is validated before it reaches the argv (frequency must be a float
inside the FM broadcast band, device index an int); nothing is ever passed
through a shell.

The audio sink is injected — `_speaker_sink` plays out the machine's sound
device by default, and tests pass a fake that just collects bytes. That seam is
also where a future HTTP-streaming endpoint plugs in: hand `start()` a sink that
feeds a queue and the page/daemon need no changes.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator

# FM broadcast band. This dongle (R820T) can't tune below ~24 MHz, so AM
# broadcast and the HF CW bands are out of reach entirely — see the project
# help for the upconverter options.
FM_BAND_MHZ = (88.0, 108.0)

# rtl_fm's wbfm preset demodulates to 32 kHz mono signed 16-bit.
AUDIO_RATE = 32000
_CHUNK_BYTES = 4096

_DEVICE_RE = re.compile(r"^\s*(\d+):\s+(\S.*?)\s*$")

Sink = Callable[[bytes], None]

_lock = threading.Lock()
_state: dict[str, Any] = {
    "proc": None,
    "freq_mhz": None,
    "log": deque(maxlen=120),
    "error": "",
}
_devices_cache: list[dict[str, Any]] | None = None


class RadioError(Exception):
    """An operator-readable reason the receiver can't do what was asked."""


def rtl_status() -> dict[str, Any]:
    """Is the rtl-sdr toolchain installed, and is audio output available?"""
    path = shutil.which("rtl_fm")
    sounddevice_present = True
    try:  # the optional live-audio extra; absent means no speaker output
        import sounddevice  # noqa: F401
    except ImportError:
        sounddevice_present = False
    return {
        "rtl_fm_present": bool(path),
        "rtl_fm_path": path or "",
        "sounddevice_present": sounddevice_present,
    }


def list_devices(refresh: bool = False) -> list[dict[str, Any]]:
    """RTL-SDR dongles plugged into this machine.

    `rtl_test` *claims* the device to probe it, so it can't run while our
    `rtl_fm` holds it — hence the cache, and the skip while running. Returns
    `[]` when there's no hardware or no toolchain; never raises, because the
    page has to render a clean empty state either way.
    """
    global _devices_cache
    if _devices_cache is not None and not refresh:
        return _devices_cache
    if _state["proc"] is not None and _state["proc"].poll() is None:
        # Busy with our own rtl_fm — report what we last enumerated.
        return _devices_cache or []
    if not shutil.which("rtl_test"):
        return []
    try:
        out = subprocess.run(
            ["rtl_test", "-t"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (out.stdout or "") + (out.stderr or "")
    devices: list[dict[str, Any]] = []
    in_list = False
    for line in text.splitlines():
        if line.startswith("Found "):
            in_list = True
            continue
        if not in_list:
            continue
        m = _DEVICE_RE.match(line)
        if not m:
            break  # the enumeration block ends at the first non-device line
        devices.append({"index": int(m.group(1)), "name": m.group(2)})
    # Only cache a *successful* scan. An empty result usually means the device
    # was momentarily busy (a stray rtl_fm, gqrx, an earlier run still exiting),
    # and caching that would strand the page on "No SDR detected" until a manual
    # rescan — which is exactly the false alarm this page must not raise.
    if devices:
        _devices_cache = devices
    return devices


@contextmanager
def _speaker_sink() -> Iterator[Sink]:
    """Play PCM out the machine's default sound device."""
    try:
        import sounddevice as sd
    except ImportError as e:  # pragma: no cover - dep hint
        raise RadioError(
            "Radio audio needs the 'sounddevice' package: "
            "uv sync --extra dev --extra live"
        ) from e
    stream = sd.RawOutputStream(samplerate=AUDIO_RATE, channels=1, dtype="int16")
    stream.start()
    try:
        yield stream.write
    finally:
        stream.stop()
        stream.close()


def _log(line: str | bytes) -> None:
    # stdout carries PCM, so the process is opened in binary mode and stderr
    # arrives as bytes — decode here or the log can't be JSON-serialised.
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    _state["log"].append(line.rstrip())


def _pump(proc: subprocess.Popen, sink_cm: Any) -> None:
    """Feed rtl_fm's stdout into the sink until the process ends."""
    try:
        with sink_cm() as write:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(_CHUNK_BYTES)
                if not chunk:
                    break
                write(chunk)
    except Exception as e:  # a dead sound device must not kill the process
        _state["error"] = str(e)
        _log(f"audio sink stopped: {e}")


def _drain_stderr(proc: subprocess.Popen) -> None:
    assert proc.stderr is not None
    for line in proc.stderr:
        _log(line)


def start(
    freq_mhz: float,
    device_index: int = 0,
    gain: float | None = None,
    sink: Any = None,
) -> dict[str, Any]:
    """Tune `freq_mhz` and start playing. Raises RadioError with a reason."""
    try:
        freq = float(freq_mhz)
    except (TypeError, ValueError) as e:
        raise RadioError(f"Not a frequency: {freq_mhz!r}") from e
    low, high = FM_BAND_MHZ
    if not low <= freq <= high:
        raise RadioError(
            f"{freq:g} MHz is outside the FM broadcast band ({low:g}–{high:g} MHz)."
        )
    device_index = int(device_index)
    if device_index < 0:
        raise RadioError(f"Not a device index: {device_index!r}")
    if not shutil.which("rtl_fm"):
        raise RadioError(
            "The rtl-sdr tools aren't installed (brew install librtlsdr)."
        )

    sink_cm = sink or _speaker_sink

    with _lock:
        if _state["proc"] is not None and _state["proc"].poll() is None:
            raise RadioError("The receiver is already running — stop it first.")
        # -M wbfm is a preset (-s 170k -r 32k -E deemp -A fast ...). Do NOT pass
        # our own -s: it would override the 170 kHz *input* rate the wideband
        # demodulator needs and leave the audio garbled. The preset's -r gives
        # AUDIO_RATE out, which is what the sink is opened at.
        argv = [
            "rtl_fm",
            "-M", "wbfm",
            "-f", f"{freq:.4f}M",
            "-d", str(device_index),
        ]
        if gain is not None:
            argv += ["-g", f"{float(gain):.1f}"]
        argv.append("-")
        _state["log"].clear()
        _state["error"] = ""
        _log("$ " + " ".join(argv))
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as e:
            raise RadioError(f"Couldn't start rtl_fm: {e}") from e
        _state["proc"] = proc
        _state["freq_mhz"] = freq
        threading.Thread(target=_pump, args=(proc, sink_cm), daemon=True).start()
        threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()

    # Give it a beat to either lock on or die (a busy/absent dongle dies at once).
    time.sleep(1.0)
    if proc.poll() is not None:
        tail = " / ".join(list(_state["log"])[-3:])
        with _lock:
            _state["proc"] = None
            _state["freq_mhz"] = None
        if "usb_claim_interface" in tail or "device is busy" in tail.lower():
            raise RadioError(
                "The SDR is in use by another program (gqrx? another tab?). "
                "Close it and try again."
            )
        raise RadioError("rtl_fm exited immediately — " + (tail or "no output"))
    return status()


def stop() -> dict[str, Any]:
    with _lock:
        proc = _state["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        _state["proc"] = None
        _state["freq_mhz"] = None
    return status()


def status() -> dict[str, Any]:
    proc = _state["proc"]
    running = proc is not None and proc.poll() is None
    return {
        "running": running,
        "pid": proc.pid if running else None,
        "freq_mhz": _state["freq_mhz"] if running else None,
        "band": {"low": FM_BAND_MHZ[0], "high": FM_BAND_MHZ[1]},
        "log": list(_state["log"])[-25:],
        "error": _state["error"],
        **rtl_status(),
    }
