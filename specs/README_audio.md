# audioengine — CW decoding + the audio seam

A hardware-independent audio decoding layer for the station manager. CW is the
first engine; the same seam is built to host fldigi/WSJT-X taps and an ML
decoder later without touching the callers.

## Why it's built this way

The whole layer is designed so **every mode speaks one event contract**
(`CharEvent`, `ElementEvent`, `KeyRun`). The logbook, the live view, and any
future consumer never learn which engine produced a character. Adding a mode
later means writing one class and registering it — that's the point of building
the seam up front.

```
 AudioSource ─▶ AudioEngineManager ─▶ [ AudioDemodulator … ]  ─┐
 (mic/wav/synth)     │  fan-out           CWDecoder (now)       ├▶ CharEvent stream
                     │                    MLDecoder (later)     │      │
                     └──▶ [ NetworkTapEngine … ]                ┘      ├▶ logbook bridge
                          fldigi / WSJT-X (later)                     └▶ live view (cw_view/)
```

## Modules

| file | role |
|------|------|
| `morse.py`   | International Morse tables; element⇄text (no DSP, unit-tested) |
| `synth.py`   | `synthesize_cw()` — PARIS-timed CW audio with edge shaping + controllable SNR. The no-hardware test/demo driver. |
| `cw.py`      | `CWDecoder` — the modern streaming decoder (see below) |
| `engine.py`  | `Engine` / `AudioDemodulator` / `NetworkTapEngine` / `AudioEngineManager` — the seam |
| `sources.py` | `SyntheticCWSource`, `WavFileSource`, `SoundDeviceSource` (live, guarded) |
| `events.py`  | the event contract + `DecodeResult` (text + telemetry) |
| `export.py`  | `DecodeResult` → compact session JSON for the view |

## The decoder (cw.py)

Pipeline: **audio → tone magnitude → smoothed envelope → adaptive keyed state →
key runs → elements → characters.**

What makes it a modern take rather than a port of the classic OZ1JHM sketch:

- **Vectorized single-bin DFT** (Goertzel-equivalent) at the target tone plus two
  neighbouring bins for a little frequency tolerance — a couple of numpy dot
  products per block.
- **Fast-attack / slow-release** peak and floor trackers drive an adaptive
  threshold with hysteresis and a debounce, so band noise doesn't shred the
  keying.
- **Fully adaptive WPM** — no fixed speed assumed — with a **two-phase
  bootstrap** that buffers the opening marks, estimates the dit length from the
  dit cluster, then replays them, so the *first* character isn't lost.
- **Streaming**: `process(chunk)` carries state across calls (live sound card) or
  runs once over a whole buffer.
- Emits **structured events + telemetry** (elements, characters, WPM, SNR,
  confidence, envelope), not just a flat string.

Limits, honestly: like all threshold decoders it's excellent on clean or
machine-sent CW and degrades on weak, fading, or irregular human sending — a
trained ear still wins there. That's the ceiling an ML engine (a future sibling
on this same seam) would raise.

## Running it — no radio required

```bash
# unit + regression tests (synthesize → decode → assert)
python -m unittest tests.test_cw_decode -v

# decode synthesized CW
python tools/cw_decode.py --text "CQ CQ DE N0CALL K" --wpm 22

# decode a WAV recorded off a receiver / exported from GQRX
python tools/cw_decode.py --wav signal.wav --tone 700

# decode + drive the logbook bridge (prints QSO drafts)
python tools/cw_decode.py --text "CQ TEST DE W1AW 599" --wpm 20 --prior --log
```

Live audio later: `pip install sounddevice` and swap in `SoundDeviceSource` —
nothing else changes.

## The live view (cw_view/cw-monitor.html)

Self-contained; open it in a browser. It renders **real decoder output** (three
sessions produced by the Python decoder, embedded as JSON) as a scrolling
paper-tape register — keyed dits/dahs pass a *now* line and each letter resolves
above its elements the instant the decoder commits it. Regenerate the sessions
any time with `tools/cw_decode.py --session`.

## Into the logbook (logbook/audio_bridge.py)

`CWLogBridge` subscribes to the manager and turns decoded text into a live QSO
draft, pulling callsigns and RST out of the copy. `on_qso_ready()` is the single
hook a Django view/Channels consumer overrides to persist or push the QSO. No
Django import here, so it stays testable.

## Next

- Finish the logbook app the earlier plan scoped: `QSO` model, SQLite **FTS5**
  search (external-content vtable + triggers), ADIF import/export — then point
  `CWLogBridge.on_qso_ready` at it.
- Wire `NetworkTapEngine` to fldigi (XML-RPC) and WSJT-X (UDP).
- Add an ML decoder as a second `AudioDemodulator` for hard signals.
