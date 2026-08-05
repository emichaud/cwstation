# MVP Design — CW Station (Morse decode/send on Django SmallStack)

*Revision of the original station-manager plan (`chirp-hrd-station-manager-plan.md`), rescoped 2026-08-05 to a shippable CW-focused MVP.*

## 1. What changed from the original plan

The original plan scoped a full station manager: CHIRP programming engine, Hamlib CAT control, orchestrated digital modes, satellite tracking. The MVP cuts all of that and ships **one thing done well: CW receive and send through the sound card**, with a modern UI an experienced operator would actually keep open.

| Original plan | MVP decision |
|---|---|
| CHIRP driver engine (GPL question) | **Cut.** No CHIRP import → no GPL derivative-work question for now. |
| Hamlib `rigctld` CAT control | **Cut.** The sound card is the only device interface in the MVP. |
| fldigi/WSJT-X orchestration | **Cut**, but the `NetworkTapEngine` seam stays so they land later without replumbing. |
| React + Tauri UI | **Cut.** SmallStack templates + vanilla JS (the reference monitor is already dependency-free vanilla JS). One less build system. |
| FastAPI vs Django | **Django SmallStack** — it's the workspace standard and gives auth, theming, help system, deploy for free. |
| Full QSO logbook (FTS5, ADIF) | **Deferred.** MVP persists *decode/send sessions* with extracted callsigns; the logbook app is the obvious next iteration. |

## 2. MVP scope

An experienced CW operator's workbench:

1. **Decode** — feed the decoder from:
   - a WAV file (recorded off a receiver / exported from GQRX),
   - synthesized text (practice + demo, no hardware),
   - *(post-MVP)* live sound-card input via optional `sounddevice`.
   Fully adaptive WPM, adaptive threshold, structured telemetry (WPM, SNR, confidence per character).
2. **Send** — text → PARIS-timed CW audio with raised-cosine keying (no clicks). Controls: WPM, sidetone pitch, Farnsworth-free MVP. Play in browser or download WAV (feed a rig's audio input, or use for code practice).
3. **Monitor** — the paper-tape live view: keyed dits/dahs scroll past a *now* line, characters resolve above their elements, WPM/SNR gauges, envelope + threshold trace. Renders real decoder output (single source of truth: Python does the DSP, JS only renders).
4. **Sessions** — every decode/send is saved: direction, text, WPM, tone, SNR, callsigns heard, full telemetry JSON (replayable in the monitor).

**Dependencies added to stock SmallStack: `numpy` + `soundfile`** (libsndfile wheel — decodes the MP3/FLAC/OGG recordings operators actually have, e.g. W1AW practice files; lazily imported, WAV needs only the stdlib). `sounddevice` is documented as an optional extra, imported lazily and guarded.

## 3. Architecture

```
apps/cw/
├── engine/            # pure Python, NO Django imports, fully typed
│   ├── morse.py       # tables, element⇄text (no DSP)
│   ├── events.py      # CharEvent / ElementEvent / KeyRun / DecodeResult
│   ├── cw.py          # CWDecoder — streaming adaptive decoder
│   ├── synth.py       # synthesize_cw() — PARIS timing, edge shaping, SNR control
│   ├── sources.py     # ArraySource / SyntheticCWSource / WavFileSource / SoundDeviceSource
│   ├── manager.py     # Engine seam: AudioDemodulator / NetworkTapEngine / AudioEngineManager
│   ├── bridge.py      # CWLogBridge → QSODraft (callsign/RST extraction), on_qso_ready() hook
│   ├── export.py      # DecodeResult → session dict for the monitor
│   └── wav.py         # float32 ⇄ WAV bytes helpers (upload decode + send download)
├── models.py          # CWSession (direction, text, wpm, tone_hz, snr_db, callsigns, telemetry JSON)
├── forms.py           # DecodeForm (wav upload | text+wpm+tone+snr), SendForm (text+wpm+tone)
├── views.py           # Monitor, Decode, Send, session list/detail/replay, audio endpoint
├── urls.py            # namespace "cw"
├── management/commands/cw_decode.py   # the CLI, as a Django management command
└── tests/             # pytest: engine regression + view tests
```

Rules preserved from the reference implementation:

- **One event contract** (`CharEvent`/`ElementEvent`/`KeyRun`) for every engine, so future fldigi/WSJT-X taps and an ML decoder are drop-in registrations.
- **`apps/cw/engine/` never imports Django** — testable standalone; `bridge.py`'s `on_qso_ready()` is the only integration hook.
- **JS renders, Python decodes** — the monitor animates the session JSON produced by the decoder; no DSP reimplemented in the browser.
- **Hardware-free testing** — synthesize → decode → assert is the regression loop.

### Typing

Everything in `apps/cw/engine/` and the app layer carries full annotations (`from __future__ import annotations`, `Literal` element kinds, typed dataclasses, no bare `Any`). `ty check` (already in SmallStack dev deps if present, else annotations + ruff) keeps honest.

## 4. UI (modern-dark theme, operator-first)

Built per `docs/skills/modern-dark-theme.md`: `.card` + `var(--card-bg)`, `var(--accent-band-bg)` hero, `var(--primary)` accents only — must render correctly across all five palettes.

- **CW Monitor** (`/cw/`) — the landing page. Hero band with live stats (WPM, SNR, tone). Paper-tape canvas. Session picker to replay any stored session.
- **Decode** (`/cw/decode/`) — two-panel card: *Off the air* (WAV upload, tone control) and *Practice* (text, WPM, tone, SNR). Result: decoded copy with per-character confidence shading, extracted callsigns/RST as badges, "open in monitor" replay.
- **Send** (`/cw/send/`) — message composer with prosign palette (`<AR> <SK> <BT> <KN>`), WPM + pitch sliders, inline audio player, WAV download. Preview shows dot/dash breakdown.
- **Sessions** (`/cw/sessions/`) — table (django-tables2 per SmallStack convention) with direction, text preview, WPM, SNR, callsigns, replay link.
- Sidebar entry + dashboard stat card (sessions decoded, characters copied, best SNR copy).

## 5. Documentation & skills (updated as we build)

- Help app: `CW Station` help page(s) — operator-facing runbook: decoding off a receiver, tone matching, why adaptive WPM sometimes misses the first char at very low SNR, sending audio into a rig safely (VOX, drive level).
- `docs/skills/cw-audioengine.md` — AI-skill doc: the event contract, how to add an engine, how to synthesize test signals, where Django is allowed.
- Update project `CLAUDE.md` (merge base SmallStack CLAUDE.md + project specifics).

## 6. Out of scope (kept alive by the seam)

Logbook app (QSO model, FTS5, ADIF) → next iteration, hangs off `on_qso_ready()`. fldigi/WSJT-X taps → `NetworkTapEngine`. ML decoder → second `AudioDemodulator`. Live mic streaming with Channels → after MVP (needs websockets; MVP is request/response + replay).
