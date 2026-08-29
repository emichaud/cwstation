"""Sweep bands with `rtl_power` and score them — the antenna comparison bench.

Swapping antennas is only meaningful if you measure the *same* bands at the
*same* gain and compare against a signal that's actually there. Three rules fall
out of that, and they're baked into the design:

- **Gain is pinned and recorded.** rtl-sdr's AGC would silently re-level between
  runs and make two antennas look identical. Surveys taken at different gains
  are flagged rather than compared.
- **Reference bands matter more than ham bands.** FM broadcast, NOAA weather,
  and the 10 m beacon segment transmit continuously, so a change in their SNR is
  a change in the antenna. An empty CW band reads 0 dB on a superb antenna and
  on a paperclip — it measures propagation, not hardware.
- **SNR, not absolute power.** A bigger antenna raises the noise floor too; what
  improves is the gap between the strongest signal and that floor.

The sweep runs in a worker thread with polled progress because a full survey is
~2 s per band and the dongle is exclusive hardware — the same reason
`radiodaemon` is shaped the way it is.
"""
from __future__ import annotations

import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .radiodaemon import RadioError

# Integration time per band. 2 s is enough for a stable median floor at these
# step sizes and keeps a full survey under ~20 s.
DWELL_S = 2
DEFAULT_GAIN_DB = 40.0
# Above this much over the band's median floor, a bin counts as a real signal.
SIGNAL_THRESHOLD_DB = 6.0


class Band:
    """One sweepable range. `reference` marks always-on transmitters — the ones
    whose SNR actually tracks antenna quality."""

    def __init__(
        self, key: str, label: str, low_mhz: float, high_mhz: float,
        step_khz: float, reference: bool = False, note: str = "",
    ) -> None:
        self.key = key
        self.label = label
        self.low_mhz = low_mhz
        self.high_mhz = high_mhz
        self.step_khz = step_khz
        self.reference = reference
        self.note = note

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label,
            "low_mhz": self.low_mhz, "high_mhz": self.high_mhz,
            "step_khz": self.step_khz, "reference": self.reference,
            "note": self.note,
        }


# Ordered low → high. The R820T tuner in the common dongles wakes up somewhere
# between 24 and 26 MHz; the bands below that are kept because "flat floor" is
# itself the measurement that tells you so.
BANDS: list[Band] = [
    Band("12m", "12 m ham", 24.89, 24.99, 1,
         note="Below most dongles' tuner floor — expect nothing"),
    Band("cb", "CB / 11 m", 26.965, 27.405, 1,
         note="Busy in daytime; a decent HF-capable antenna shows traffic"),
    Band("10m_cw", "10 m CW", 28.0, 28.07, 1,
         note="The CW segment — propagation-dependent"),
    Band("10m_beacon", "10 m beacons", 28.2, 28.3, 1, reference=True,
         note="IBP beacons transmit around the clock — a good antenna check"),
    Band("10m_ssb", "10 m SSB", 28.3, 28.5, 2),
    Band("6m", "6 m ham", 50.0, 50.3, 2),
    Band("fm", "FM broadcast", 88.0, 108.0, 20, reference=True,
         note="Always on and local — the easiest signal there is"),
    Band("air", "Airband", 118.0, 137.0, 20,
         note="Bursty; low readings may just mean no traffic"),
    Band("2m", "2 m ham", 144.0, 148.0, 5),
    Band("noaa", "NOAA weather", 162.4, 162.56, 2, reference=True,
         note="Transmits 24/7 — the single best antenna comparison signal"),
]

BANDS_BY_KEY = {b.key: b for b in BANDS}
DEFAULT_BAND_KEYS = ["10m_beacon", "10m_cw", "fm", "noaa", "2m"]

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current": "",
    "results": [],
    "error": "",
    "antenna": "",
    "gain_db": DEFAULT_GAIN_DB,
    "survey_id": None,
}


def bands_payload() -> list[dict[str, Any]]:
    return [b.as_dict() for b in BANDS]


def status() -> dict[str, Any]:
    with _lock:
        return dict(_state, results=list(_state["results"]))


def sweep_band(band: Band, gain_db: float) -> dict[str, Any]:
    """One rtl_power pass over `band`, scored. Raises RadioError on failure."""
    if not shutil.which("rtl_power"):
        raise RadioError(
            "Scanning needs rtl_power from the rtl-sdr tools (brew install librtlsdr)."
        )
    out_path = Path(tempfile.gettempdir()) / f"cw-bandscan-{band.key}.csv"
    argv = [
        "rtl_power",
        "-f", f"{band.low_mhz:g}M:{band.high_mhz:g}M:{band.step_khz:g}k",
        "-i", str(DWELL_S), "-1",
        "-g", f"{gain_db:.1f}",
        str(out_path),
    ]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=DWELL_S + 40)
    except subprocess.TimeoutExpired as e:
        raise RadioError(f"{band.label}: sweep timed out") from e
    except OSError as e:
        raise RadioError(f"{band.label}: couldn't run rtl_power ({e})") from e

    try:
        text = out_path.read_text()
    except OSError as e:
        raise RadioError(f"{band.label}: no sweep output ({e})") from e
    finally:
        out_path.unlink(missing_ok=True)

    return summarize(band, _parse_in_band(text, band))


def _parse_in_band(text: str, band: Band) -> list[tuple[float, float]]:
    """rtl_power CSV → [(mhz, dB)] clipped to this band.

    Not radiodaemon.parse_power_csv: that one clips to the FM broadcast band,
    which would discard every sweep here.
    """
    bins: list[tuple[float, float]] = []
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
            if band.low_mhz <= mhz <= band.high_mhz:
                bins.append((mhz, db))
    bins.sort()
    return bins


def summarize(band: Band, bins: list[tuple[float, float]]) -> dict[str, Any]:
    """Score one band: floor, strongest peak, and how many bins clear the gate.

    `snr_db` — peak minus median floor — is the number to compare between
    antennas. The absolute floor is reported too because a bigger antenna
    hearing more atmospheric noise is itself a signal that it's working.
    """
    if len(bins) < 4:
        return {
            **band.as_dict(),
            "floor_db": None, "peak_db": None, "snr_db": None,
            "peak_mhz": None, "signals": 0, "bins": len(bins),
        }
    dbs = [db for _, db in bins]
    floor = statistics.median(dbs)
    peak_mhz, peak_db = max(bins, key=lambda pair: pair[1])
    return {
        **band.as_dict(),
        "floor_db": round(floor, 1),
        "peak_db": round(peak_db, 1),
        "snr_db": round(peak_db - floor, 1),
        "peak_mhz": round(peak_mhz, 4),
        "signals": sum(1 for db in dbs if db - floor >= SIGNAL_THRESHOLD_DB),
        "bins": len(bins),
    }


def verdict(snr_db: float | None) -> str:
    """Plain-language reading of a band's SNR, so the page doesn't ask the
    operator to interpret decibels."""
    if snr_db is None:
        return "no data"
    if snr_db < 3:
        return "nothing heard"
    if snr_db < 10:
        return "faint"
    if snr_db < 20:
        return "workable"
    return "strong"


def start(
    antenna: str,
    band_keys: list[str],
    gain_db: float = DEFAULT_GAIN_DB,
    on_finish: Any = None,
) -> dict[str, Any]:
    """Kick off a survey in a worker thread. Raises RadioError if it can't."""
    from . import radiodaemon

    antenna = (antenna or "").strip()
    if not antenna:
        raise RadioError("Name the antenna so runs can be told apart.")
    bands = [BANDS_BY_KEY[k] for k in band_keys if k in BANDS_BY_KEY]
    if not bands:
        raise RadioError("Pick at least one band to sweep.")
    if radiodaemon.status()["running"]:
        raise RadioError(
            "The receiver is playing — press Stop on the FM Radio page first "
            "(the SDR can only do one thing at a time)."
        )
    if not radiodaemon.list_devices():
        raise RadioError("No SDR detected — plug a dongle in and try again.")

    with _lock:
        if _state["running"]:
            raise RadioError("A survey is already running.")
        _state.update(
            running=True, done=0, total=len(bands), current=bands[0].label,
            results=[], error="", antenna=antenna, gain_db=float(gain_db),
            survey_id=None,
        )

    def worker() -> None:
        results: list[dict[str, Any]] = []
        try:
            for band in bands:
                with _lock:
                    _state["current"] = band.label
                results.append(sweep_band(band, gain_db))
                with _lock:
                    _state["done"] = len(results)
                    _state["results"] = list(results)
        except RadioError as e:
            with _lock:
                _state["error"] = str(e)
        except Exception as e:  # a scan must never take the server down
            with _lock:
                _state["error"] = f"Sweep failed: {e}"
        finally:
            survey_id = None
            if on_finish is not None and results and not _state["error"]:
                try:
                    survey_id = on_finish(antenna, float(gain_db), results)
                except Exception as e:  # persistence is not worth losing the run
                    with _lock:
                        _state["error"] = f"Scanned, but couldn't save: {e}"
            with _lock:
                _state["running"] = False
                _state["current"] = ""
                _state["survey_id"] = survey_id

    threading.Thread(target=worker, daemon=True).start()
    time.sleep(0.1)  # let the first band register so the UI shows progress
    return status()


__all__ = [
    "BANDS", "BANDS_BY_KEY", "DEFAULT_BAND_KEYS", "DEFAULT_GAIN_DB",
    "Band", "bands_payload", "start", "status", "summarize", "sweep_band",
    "verdict",
]
