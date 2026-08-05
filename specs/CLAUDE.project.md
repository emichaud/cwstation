# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Greenfield — no application code yet.** This directory contains only `specs/`. The Django project has not been scaffolded. When it's time to start building, follow the workspace convention (see the workspace `CLAUDE.md` one level up): clone https://github.com/emichaud/django-smallstack into this directory, detach from upstream, then `make setup`. All the standard SmallStack commands (`make run` on port 8005, `make test`, `make lint`) apply once scaffolded.

## What This Project Is

A ham radio station manager with CW (Morse code) decoding as the first deliverable. The product vision (see `specs/chirp-hrd-station-manager-plan.md`) is the tool that neither CHIRP nor Ham Radio Deluxe is: CHIRP's radio-programming breadth plus HRD's operating/logging suite, cross-platform (Linux Mint / macOS), with logging and config versioning as headline features.

## The Specs Directory

- `specs/chirp-hrd-station-manager-plan.md` — the overall architecture and phased build plan. Key decisions already made:
  - **Reuse, don't fork**: import CHIRP as a Python library for radio programming (~350 drivers); run Hamlib `rigctld` (TCP :4532) for live CAT control; orchestrate WSJT-X (UDP) and fldigi (XML-RPC) for digital modes rather than owning DSP.
  - **Local Python backend + web UI** (REST/WebSocket) for cross-platform reach; SQLite default, Postgres optional.
  - **Open licensing question**: importing CHIRP makes the app a GPL derivative work. This must be decided in Phase 0 before writing much code — it shapes everything downstream.
- `specs/ham-audioengine.tar.gz` — a **working reference implementation** of the CW decoding layer (extracts to `ham-logbook/`). Contains the `audioengine/` package, tests, `tools/cw_decode.py` CLI, and `logbook/audio_bridge.py`. Extract with `tar -xzf specs/ham-audioengine.tar.gz`.
- `specs/README_audio.md` — documentation for that reference implementation (module map, decoder design, how to run it with no radio attached).
- `specs/cw-monitor.html` — self-contained live-view demo: a scrolling paper-tape register rendering real decoder output (embedded JSON sessions). Open directly in a browser; regenerate sessions with `tools/cw_decode.py --session`.

## Audio Engine Architecture (from the reference implementation)

These design decisions are deliberate and should be preserved when integrating into Django:

- **One event contract for every mode**: all engines emit `CharEvent` / `ElementEvent` / `KeyRun`. Consumers (logbook, live view) never know which engine produced a character. Adding a mode = write one class and register it.
- **The seam**: `AudioSource` (mic/wav/synth) → `AudioEngineManager` (fan-out) → `AudioDemodulator` engines (CWDecoder now, ML decoder later) and `NetworkTapEngine` (fldigi/WSJT-X taps later) → event stream.
- **CWDecoder pipeline**: audio → vectorized single-bin DFT (target tone + two neighbor bins) → smoothed envelope → adaptive threshold with hysteresis/debounce → key runs → elements → characters. Fully adaptive WPM with a two-phase bootstrap so the first character isn't lost. Streaming: `process(chunk)` carries state across calls.
- **`CWLogBridge` has no Django imports** — `on_qso_ready()` is the single hook a Django view/Channels consumer overrides to persist or push a QSO. Keep the audio layer Django-free and testable.
- **Tests are synthesize → decode → assert**: `synth.py` generates PARIS-timed CW with controllable SNR, so the decoder is regression-tested with no hardware. Reference test invocation: `python -m unittest tests.test_cw_decode -v` (from the extracted tarball).

## Planned Next Steps (from the specs)

1. Logbook app: `QSO` model, SQLite **FTS5** search (external-content vtable + triggers), ADIF import/export — then point `CWLogBridge.on_qso_ready` at it.
2. Wire `NetworkTapEngine` to fldigi (XML-RPC) and WSJT-X (UDP).
3. ML decoder as a second `AudioDemodulator` on the same seam for weak/irregular signals.
