"""Launch and supervise a local `rtl_fm` for broadcast FM listening.

The FM Radio page's engine — the receiver sibling of `rigdaemon.py`: enumerate
the RTL-SDR dongles present, start `rtl_fm` on a chosen frequency, pump its
audio at a sink, seek along the band with an `rtl_power` sweep, keep the recent
log lines, and stop it. One managed receiver per server process; the dongle is
exclusive hardware.

Everything is validated before it reaches the argv (frequency must be a float
inside the FM broadcast band, device index an int); nothing is ever passed
through a shell.

Three structural rules, learned the hard way:

- **Operations are serialised.** `_op_lock` wraps whole tune/stop/seek
  operations, so two racing requests can never interleave a stop with a start
  and leave an untracked rtl_fm playing forever.
- **The child's PID goes in a pidfile.** The dev server's autoreloader restarts
  this module while the spawned rtl_fm keeps running — the fresh process then
  knows nothing about it, and Stop stops nothing while static keeps playing.
  `_reap_stale()` reads the pidfile, verifies the process is really rtl_fm, and
  kills it before every start/stop.
- **The audio sink is injected.** `_speaker_sink` plays out the machine's sound
  device by default; tests pass a fake that collects bytes; a future
  HTTP-streaming endpoint hands `start()` a queue-backed sink with no page or
  daemon changes. On close the stream *aborts* (discards queued audio) so Stop
  is silence now, not after the buffer drains.
"""
from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

# FM broadcast band. This dongle (R820T) can't tune below ~24 MHz, so AM
# broadcast and the HF CW bands are out of reach entirely — see the project
# help for the upconverter options.
FM_BAND_MHZ = (88.0, 108.0)

# rtl_fm's wbfm preset demodulates to 32 kHz mono signed 16-bit.
AUDIO_RATE = 32000
_CHUNK_BYTES = 4096

# Seek: a station must rise this far above the band's median floor to count.
# Real FM carriers here measured +30 dB and up; empty channels sit within a few
# dB of the floor, so 12 gives a wide margin on both sides.
SEEK_THRESHOLD_DB = 12.0
SEEK_MIN_SEPARATION_MHZ = 0.25

_DEVICE_RE = re.compile(r"^\s*(\d+):\s+(\S.*?)\s*$")
_PIDFILE = Path(tempfile.gettempdir()) / "cw-radio-rtl_fm.pid"

Sink = Callable[[bytes], None]

_op_lock = threading.Lock()  # serialises whole operations (tune/stop/seek)
_lock = threading.Lock()  # guards _state mutation
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
    try:
        # The optional live-audio extra. ImportError = package absent;
        # OSError = package present but PortAudio itself isn't (a headless
        # Linux box without libportaudio2). Either way there's no speaker.
        import sounddevice  # noqa: F401
    except (ImportError, OSError):
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
        devices.append({
            "index": int(m.group(1)),
            "name": m.group(2),
            "serial": _serial_from_name(m.group(2)),
        })
    # rtl_test probes only the first device, so the gain table and tuner it
    # reports belong to device 0. Attaching it to every entry would be a lie
    # when two different sticks are plugged in.
    gains = _gains_from_probe(text)
    tuner = _tuner_from_probe(text)
    if devices:
        devices[0]["gains"] = gains
        devices[0]["tuner"] = tuner
        devices[0]["direct_sampling"] = supports_direct_sampling(devices[0]["name"])
    for extra in devices[1:]:
        extra["gains"] = []
        extra["tuner"] = ""
        extra["direct_sampling"] = supports_direct_sampling(extra["name"])
    # Only cache a *successful* scan. An empty result usually means the device
    # was momentarily busy (a stray rtl_fm, gqrx, an earlier run still exiting),
    # and caching that would strand the page on "No SDR detected" until a manual
    # rescan — which is exactly the false alarm this page must not raise.
    if devices:
        _devices_cache = devices
    return devices


_GAINS_RE = re.compile(r"Supported gain values \(\d+\):\s*(.+)")
# multi-word: "Found Rafael Micro R820T tuner"
_TUNER_RE = re.compile(r"Found (.+?) tuner")
_SERIAL_RE = re.compile(r"SN:\s*(\S+)")

# Sticks whose HF coverage works through direct sampling. The ADC tap is a
# hardware modification: the RTL-SDR Blog V3/V4 ship with it, most others
# (including the NESDR SMArt) don't, and no software switch substitutes.
_DIRECT_SAMPLING_HINTS = ("rtl-sdr blog", "rtlsdrblog", "blog v3", "blog v4")

# Below roughly this, the R820T/R828D tuners have no signal path at all; HF
# needs either direct sampling or an upconverter.
TUNER_FLOOR_MHZ = 24.0


def supports_direct_sampling(device_name: str) -> bool:
    """Whether this stick is known to have the ADC tap HF needs.

    Name-based because librtlsdr exposes no capability bit. A false negative
    just means the operator has to tick the box themselves; a false positive
    would silently produce noise, so the list stays conservative.
    """
    lowered = (device_name or "").lower()
    return any(hint in lowered for hint in _DIRECT_SAMPLING_HINTS)


def _serial_from_name(name: str) -> str:
    m = _SERIAL_RE.search(name or "")
    return m.group(1) if m else ""


def _gains_from_probe(text: str) -> list[float]:
    """The tuner's real gain steps. rtl_fm/rtl_power snap to the nearest, so a
    UI offering arbitrary values would record a gain that wasn't used."""
    m = _GAINS_RE.search(text or "")
    if not m:
        return []
    values: list[float] = []
    for token in m.group(1).split():
        try:
            values.append(float(token))
        except ValueError:
            break
    return values


def _tuner_from_probe(text: str) -> str:
    m = _TUNER_RE.search(text or "")
    return m.group(1) if m else ""


def nearest_gain(wanted: float, gains: list[float]) -> float:
    """Snap a requested gain to what the tuner can actually do."""
    if not gains:
        return float(wanted)
    return min(gains, key=lambda g: abs(g - float(wanted)))


@contextmanager
def _speaker_sink() -> Iterator[Sink]:
    """Play PCM out the machine's default sound device."""
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:  # pragma: no cover - dep hint
        raise RadioError(
            "Radio audio needs the 'sounddevice' package and PortAudio: "
            "uv sync --extra dev --extra live"
        ) from e
    stream = sd.RawOutputStream(samplerate=AUDIO_RATE, channels=1, dtype="int16")
    stream.start()
    try:
        yield stream.write
    finally:
        # abort(), not stop(): stop() plays out whatever is buffered, which is
        # exactly the "I pressed Stop and it kept hissing" complaint.
        stream.abort()
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


# ── orphan reaping ─────────────────────────────────────────────────────────


def _process_command(pid: int) -> str:
    """The command name for a live PID, "" when it doesn't exist."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def _reap_stale() -> None:
    """Kill an rtl_fm left over from a previous server process.

    The autoreloader restarts Django while the spawned rtl_fm keeps playing;
    the new process's _state knows nothing about it, so Stop would stop
    nothing. The pidfile bridges the restart. The PID is only signalled if its
    command really is rtl_fm — PIDs get recycled.
    """
    try:
        pid = int(_PIDFILE.read_text().strip())
    except (OSError, ValueError):
        return
    proc = _state["proc"]
    if proc is not None and proc.poll() is None and proc.pid == pid:
        return  # that's the receiver we're supervising, not a stale orphan
    if pid > 0 and _process_command(pid).endswith("rtl_fm"):
        try:
            os.kill(pid, 15)
            for _ in range(20):  # up to ~2s for a clean exit
                if not _process_command(pid).endswith("rtl_fm"):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, 9)
        except OSError:
            pass
        _log(f"reaped stale rtl_fm (pid {pid})")
    try:
        _PIDFILE.unlink()
    except OSError:
        pass


# ── lifecycle (all public entry points hold _op_lock) ──────────────────────


def _validate_freq(freq_mhz: Any) -> float:
    try:
        freq = float(freq_mhz)
    except (TypeError, ValueError) as e:
        raise RadioError(f"Not a frequency: {freq_mhz!r}") from e
    low, high = FM_BAND_MHZ
    if not low <= freq <= high:
        raise RadioError(
            f"{freq:g} MHz is outside the FM broadcast band ({low:g}–{high:g} MHz)."
        )
    return freq


def _start_locked(
    freq: float,
    device_index: int = 0,
    gain: float | None = None,
    sink: Any = None,
    direct_sampling: bool = False,
) -> dict[str, Any]:
    device_index = int(device_index)
    if device_index < 0:
        raise RadioError(f"Not a device index: {device_index!r}")
    if not shutil.which("rtl_fm"):
        raise RadioError(
            "The rtl-sdr tools aren't installed (brew install librtlsdr)."
        )

    sink_cm = sink or _speaker_sink
    _reap_stale()

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
        # HF below the tuner floor only exists via the ADC tap. rtl_fm spells
        # this "-E direct2"; rtl_power spells the same thing "-D" (checked
        # against both binaries — they are not interchangeable).
        if direct_sampling:
            argv += ["-E", "direct2"]
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
        try:
            _PIDFILE.write_text(str(proc.pid))
        except OSError:
            pass
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


def _stop_locked() -> dict[str, Any]:
    with _lock:
        proc = _state["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            # Unblock the pump immediately so the sink aborts now rather than
            # after it drains whatever rtl_fm had buffered.
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except OSError:
                pass
        _state["proc"] = None
        _state["freq_mhz"] = None
    _reap_stale()
    return status()


def start(
    freq_mhz: float,
    device_index: int = 0,
    gain: float | None = None,
    sink: Any = None,
    direct_sampling: bool = False,
) -> dict[str, Any]:
    """Tune `freq_mhz` and start playing. Raises RadioError with a reason."""
    freq = _validate_freq(freq_mhz)
    with _op_lock:
        return _start_locked(freq, device_index, gain, sink, direct_sampling)


def stop() -> dict[str, Any]:
    with _op_lock:
        return _stop_locked()


def retune(
    freq_mhz: float,
    device_index: int = 0,
    gain: float | None = None,
    sink: Any = None,
    direct_sampling: bool = False,
) -> dict[str, Any]:
    """Stop-then-start as ONE serialised operation.

    Validates first, so a bad frequency refuses without killing what's
    currently playing. rtl_fm has no runtime tuning channel and the dongle is
    exclusive, so the old process must release it before the new one claims.
    """
    freq = _validate_freq(freq_mhz)
    with _op_lock:
        _stop_locked()
        time.sleep(0.2)  # let libusb release the interface
        return _start_locked(freq, device_index, gain, sink, direct_sampling)


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


# ── seek: sweep the band, find the strong carriers ─────────────────────────


def parse_power_csv(text: str) -> list[tuple[float, float]]:
    """rtl_power CSV → sorted [(freq_mhz, dB)] bins inside the FM band.

    Each line: date, time, hz_low, hz_high, hz_step, samples, dB, dB, ...
    One sweep spans several lines (the dongle hops in ~2 MHz chunks).
    """
    bins: list[tuple[float, float]] = []
    low_mhz, high_mhz = FM_BAND_MHZ
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            hz_low, hz_step = float(parts[2]), float(parts[4])
            dbs = [float(p) for p in parts[6:]]
        except ValueError:
            continue
        for i, db in enumerate(dbs):
            mhz = (hz_low + (i + 0.5) * hz_step) / 1e6
            if low_mhz <= mhz <= high_mhz:
                bins.append((mhz, db))
    bins.sort()
    return bins


def find_stations(
    bins: list[tuple[float, float]],
    threshold_db: float = SEEK_THRESHOLD_DB,
) -> list[tuple[float, float]]:
    """Peaks that rise `threshold_db` above the band's median floor.

    Greedy strongest-first with a minimum separation, so one wide carrier
    doesn't register as three stations. Frequencies snap to the 0.1 MHz grid
    broadcasters actually use. Returns [(freq_mhz, snr_db)] sorted by freq.
    """
    if len(bins) < 8:
        return []
    dbs = [db for _, db in bins]
    floor = statistics.median(dbs)
    candidates = []
    for i in range(1, len(bins) - 1):
        mhz, db = bins[i]
        if db - floor < threshold_db:
            continue
        if db >= bins[i - 1][1] and db >= bins[i + 1][1]:  # local max
            candidates.append((db - floor, mhz))
    candidates.sort(reverse=True)
    kept: list[tuple[float, float]] = []
    for snr, mhz in candidates:
        if any(abs(mhz - k) < SEEK_MIN_SEPARATION_MHZ for k, _ in kept):
            continue
        kept.append((round(mhz, 1), round(snr, 1)))
    kept.sort()
    return kept


def next_station(
    freqs: list[float], current: float, direction: str
) -> float | None:
    """The nearest station strictly beyond `current`, wrapping at band edges.

    A small guard band keeps "seek" from re-finding the station it's sitting
    on when the peak snapped a bin away from the dial.
    """
    eps = 0.15
    if not freqs:
        return None
    if direction == "up":
        ahead = [f for f in freqs if f > current + eps]
        target = min(ahead) if ahead else min(freqs)
    else:
        behind = [f for f in freqs if f < current - eps]
        target = max(behind) if behind else max(freqs)
    if abs(target - current) <= eps:
        return None  # the only station on the band is the one we're on
    return target


def _run_sweep(device_index: int) -> list[tuple[float, float]]:
    """One rtl_power pass over the FM band. Claims the dongle for a few
    seconds — callers must have stopped rtl_fm first."""
    if not shutil.which("rtl_power"):
        raise RadioError(
            "Seeking needs rtl_power from the rtl-sdr tools (brew install librtlsdr)."
        )
    argv = [
        "rtl_power",
        "-f", f"{FM_BAND_MHZ[0]:g}M:{FM_BAND_MHZ[1]:g}M:100k",
        "-i", "1", "-1",
        "-d", str(int(device_index)),
        "-",
    ]
    _log("$ " + " ".join(argv))
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as e:
        raise RadioError("The band sweep timed out.") from e
    except OSError as e:
        raise RadioError(f"Couldn't run rtl_power: {e}") from e
    bins = parse_power_csv(out.stdout)
    if not bins:
        tail = (out.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise RadioError(f"The band sweep produced nothing — {tail[0]}")
    return bins


def seek(
    direction: str,
    from_mhz: float,
    device_index: int = 0,
    sink: Any = None,
) -> dict[str, Any]:
    """Scan to the next strong station and start playing it.

    Stops the receiver (the dongle is exclusive), sweeps the whole band once
    with rtl_power, skips everything under the threshold, tunes the next
    carrier in `direction` (wrapping at the band edge). If nothing qualifies,
    resumes whatever was playing before and raises.
    """
    if direction not in ("up", "down"):
        raise RadioError("direction must be 'up' or 'down'")
    current = _validate_freq(from_mhz)
    with _op_lock:
        was_playing = None
        if _state["proc"] is not None and _state["proc"].poll() is None:
            was_playing = _state["freq_mhz"]
        _stop_locked()
        time.sleep(0.2)
        bins = _run_sweep(device_index)
        stations = find_stations(bins)
        target = next_station([f for f, _ in stations], current, direction)
        if target is None:
            if was_playing is not None:
                _start_locked(was_playing, device_index, sink=sink)
            raise RadioError("No strong station found — nothing above the noise.")
        state = _start_locked(target, device_index, sink=sink)
        state["seek"] = {
            "found": target,
            "stations": stations,
        }
        return state
