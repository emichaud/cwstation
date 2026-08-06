"""Launch and supervise a local Hamlib `rigctld` daemon.

The Rig Setup page's engine: enumerate the machine's serial ports, list
Hamlib's rig catalog (`rigctl -l`), start `rigctld` with a chosen
model/port/baud, keep its recent log lines, and stop it. One managed daemon
per server process — a station has one radio on one port.

Everything is validated before it reaches the argv (model must be an int
from the catalog, the serial device must exist and look like /dev/...,
baud from a fixed set); nothing is ever passed through a shell.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from typing import Any

from .rigctl import RigctldClient, RigError

VALID_BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
SERIAL_DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9._\-]+$")
DUMMY_MODEL = 1

_lock = threading.Lock()
_state: dict[str, Any] = {"proc": None, "spec": None, "log": deque(maxlen=120)}


def hamlib_status() -> dict[str, Any]:
    """Is Hamlib installed, and which version?"""
    path = shutil.which("rigctld")
    if not path:
        return {"installed": False, "version": ""}
    try:
        out = subprocess.run(
            ["rigctld", "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip().splitlines()
        version = out[0] if out else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    return {"installed": True, "version": version, "path": path}


def list_serial_ports() -> list[dict[str, str]]:
    """USB-serial devices a rig could be on (macOS `cu.*`, Linux ttyUSB/ACM).
    Bluetooth pseudo-ports are filtered out; a hint names the likely bridge
    chip so the operator can tell a radio from an Arduino."""
    candidates: list[str] = []
    for pattern in ("/dev/cu.*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(glob.glob(pattern))
    ports = []
    for dev in sorted(set(candidates)):
        name = os.path.basename(dev)
        if "Bluetooth" in name or "debug-console" in name:
            continue
        hint = ""
        lowered = name.lower()
        if "slab" in lowered or "cp21" in lowered:
            hint = "Silicon Labs bridge — common in Icom/Yaesu USB ports"
        elif "usbserial" in lowered or "ftdi" in lowered:
            hint = "FTDI/generic USB-serial adapter"
        elif "usbmodem" in lowered or "ttyacm" in lowered:
            hint = "USB CDC device (some rigs, keyers, Arduinos)"
        ports.append({"device": dev, "hint": hint})
    return ports


_models_cache: list[dict[str, str]] | None = None


def list_models(refresh: bool = False) -> list[dict[str, str]]:
    """Hamlib's rig catalog from `rigctl -l`: number, manufacturer, model."""
    global _models_cache
    if _models_cache is not None and not refresh:
        return _models_cache
    if not shutil.which("rigctl"):
        return []
    try:
        out = subprocess.run(
            ["rigctl", "-l"], capture_output=True, text=True, timeout=15
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    models: list[dict[str, str]] = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\S.*?)\s{2,}", line)
        if m:
            models.append({
                "id": int(m.group(1)),
                "mfg": m.group(2).strip(),
                "model": m.group(3).strip(),
            })
    _models_cache = models
    return models


def _port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _reader(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        _state["log"].append(line.rstrip())


def start(
    model: int,
    serial_port: str | None = None,
    baud: int | None = None,
    tcp_port: int = 4532,
) -> dict[str, Any]:
    """Launch rigctld. Raises RigError with an operator-readable reason."""
    model = int(model)
    if model != DUMMY_MODEL and serial_port:
        if not SERIAL_DEVICE_RE.fullmatch(serial_port):
            raise RigError(f"Not a serial device path: {serial_port!r}")
        if not os.path.exists(serial_port):
            raise RigError(f"{serial_port} doesn't exist — is the rig plugged in?")
    if baud is not None and int(baud) not in VALID_BAUDS:
        raise RigError(f"Unsupported baud {baud}")
    if not shutil.which("rigctld"):
        raise RigError("Hamlib isn't installed (brew install hamlib / apt install hamlib-utils)")

    with _lock:
        if _state["proc"] is not None and _state["proc"].poll() is None:
            raise RigError("A managed rigctld is already running — stop it first.")
        if _port_in_use(tcp_port):
            raise RigError(
                f"Something is already listening on :{tcp_port} — an unmanaged "
                "rigctld? Stop it, or use a different port."
            )
        argv = ["rigctld", "-m", str(model), "-t", str(tcp_port)]
        if serial_port and model != DUMMY_MODEL:
            argv += ["-r", serial_port]
        if baud and model != DUMMY_MODEL:
            argv += ["-s", str(int(baud))]
        _state["log"].clear()
        _state["log"].append("$ " + " ".join(argv))
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        _state["proc"] = proc
        _state["spec"] = {
            "model": model, "serial_port": serial_port or "",
            "baud": baud or 0, "tcp_port": tcp_port,
        }
        threading.Thread(target=_reader, args=(proc,), daemon=True).start()

    # give it a beat to either bind or die (bad port/model dies immediately)
    time.sleep(0.8)
    if proc.poll() is not None:
        raise RigError(
            "rigctld exited immediately — " + (" / ".join(list(_state["log"])[-3:]) or "no output")
        )
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
        _state["spec"] = None
    return status()


def status() -> dict[str, Any]:
    proc = _state["proc"]
    running = proc is not None and proc.poll() is None
    result: dict[str, Any] = {
        "running": running,
        "pid": proc.pid if running else None,
        "spec": _state["spec"] if running else None,
        "log": list(_state["log"])[-25:],
        "reachable": False,
    }
    if running and _state["spec"]:
        try:
            with RigctldClient("127.0.0.1", _state["spec"]["tcp_port"], timeout=1.5) as c:
                result.update(c.status())
                result["reachable"] = True
        except RigError as e:
            result["probe_error"] = str(e)
    return result
