"""A minimal Hamlib `rigctld` client — real rig control over TCP.

rigctld (LGPL, ships with Hamlib: `brew install hamlib` / `apt install
hamlib-utils`) daemonizes CAT control for ~250 transceivers and speaks a
simple text protocol on TCP :4532. This client covers what a CW station
needs: frequency, mode, and PTT. No Django imports — tested against a fake
rigctld server, works identically against the real one.

Run the daemon on the machine wired to the radio:

    rigctld -m 1                          # dummy rig — for testing
    rigctld -m 3085 -r /dev/ttyUSB0       # e.g. an IC-7300 on USB serial
"""
from __future__ import annotations

import socket


class RigError(Exception):
    """rigctld unreachable, or the rig rejected a command."""


class RigctldClient:
    """One TCP connection to rigctld. Use as a context manager."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4532, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    # -- connection ----------------------------------------------------------
    def connect(self) -> "RigctldClient":
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RigError(f"rigctld unreachable at {self.host}:{self.port} ({e})") from e
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "RigctldClient":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- protocol ------------------------------------------------------------
    def _readline(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(4096)
            except OSError as e:
                raise RigError(f"rigctld read failed ({e})") from e
            if not chunk:
                raise RigError("rigctld closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("ascii", "replace").strip()

    def _request(self, command: str, reply_lines: int) -> list[str]:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        try:
            self._sock.sendall(command.encode("ascii") + b"\n")
        except OSError as e:
            raise RigError(f"rigctld write failed ({e})") from e
        lines: list[str] = []
        for _ in range(reply_lines):
            line = self._readline()
            if line.startswith("RPRT"):
                code = line.split()[-1]
                if code != "0":
                    raise RigError(f"rig rejected {command.split()[0]!r} (RPRT {code})")
                return lines  # early RPRT 0 ends a set command
            lines.append(line)
        return lines

    # -- the CW station's vocabulary ----------------------------------------
    def get_freq(self) -> int:
        """Dial frequency in Hz."""
        return int(float(self._request("f", 1)[0]))

    def set_freq(self, hz: int) -> None:
        self._request(f"F {int(hz)}", 1)

    def get_mode(self) -> tuple[str, int]:
        """(mode, passband_hz) — e.g. ("CW", 500)."""
        lines = self._request("m", 2)
        passband = int(float(lines[1])) if len(lines) > 1 else 0
        return lines[0], passband

    def set_mode(self, mode: str, passband_hz: int = 0) -> None:
        self._request(f"M {mode} {int(passband_hz)}", 1)

    def get_ptt(self) -> bool:
        return self._request("t", 1)[0].strip() == "1"

    def set_ptt(self, on: bool) -> None:
        self._request(f"T {1 if on else 0}", 1)

    def status(self) -> dict[str, object]:
        """One probe for the UI: frequency, mode, PTT."""
        mode, passband = self.get_mode()
        return {
            "freq_hz": self.get_freq(),
            "mode": mode,
            "passband_hz": passband,
            "ptt": self.get_ptt(),
        }
