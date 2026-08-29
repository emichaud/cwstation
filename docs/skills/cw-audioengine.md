# Skill: CW Audio Engine (apps/cw)

**Read this before touching anything in `apps/cw/`** — decoder, synthesizer, sessions,
monitor UI, or when adding a new decode mode. The layer has three hard rules; breaking
them is the main way changes here go wrong.

## The three rules

1. **`apps/cw/engine/` never imports Django.** It's a pure, fully-typed Python package
   (numpy only) so it stays unit-testable without a database or settings. The single
   integration hook toward the web app is `bridge.CWLogBridge.on_qso_ready()`; the only
   module allowed to import both sides is `apps/cw/services.py`.
2. **One event contract for every engine.** All decoders emit
   `CharEvent` / `ElementEvent` / `KeyRun` (see `engine/events.py`). Consumers — the
   session model, the monitor, the bridge — never learn which engine produced a
   character. Add a mode by subclassing and registering, never by teaching a consumer
   about a new engine.
3. **Python decodes, JavaScript renders.** The monitor (`static/cw/monitor.js`)
   animates the session dict produced by `engine/export.py`. Never re-implement DSP or
   timing logic in the browser; if the view needs new data, add it to the telemetry
   export. (The WebAudio sidetone in `monitor.js` is playback, not DSP — it sounds the
   decoder's key runs; same for the progressive send-coloring, driven by stored
   `CharEvent` timings.)

## Map

| Module | Role |
|---|---|
| `engine/morse.py` | Morse tables, element⇄text, inline prosigns (`<AR>` = one symbol). No DSP. |
| `engine/events.py` | The event contract + `DecodeResult` (text + replay telemetry). |
| `engine/cw.py` | `CWDecoder` — streaming: tone DFT → envelope → adaptive threshold → key runs → elements → chars. Fully adaptive WPM with a two-phase bootstrap. Operator knobs on `CWConfig` (all live-mutable): `input_gain`, `squelch_db` (SNR gate against noise false-positives), and `afc` (chases the strongest carrier; re-tunes only between marks, gated on peak prominence so noise isn't chased — note the 4 ms blocks give 250 Hz DFT bins, so off-pitch stations are partially heard even without AFC; AFC's value is exact centering). |
| `engine/synth.py` | `synthesize_cw()` — PARIS timing, raised-cosine edges, controllable SNR. The no-hardware test driver AND the transmit path. |
| `engine/audio_io.py` | `load_audio()` — WAV via stdlib, MP3/FLAC/OGG via `soundfile`; `detect_tone()` — spectral peak finder so operators don't guess the pitch. |
| `engine/sources.py` | `ArraySource` / `SyntheticCWSource` / `AudioFileSource` (any format) / `SoundDeviceSource` (guarded optional). |
| `engine/manager.py` | The seam: `AudioDemodulator`, `NetworkTapEngine`, `AudioEngineManager` (fan-out + subscribers). |
| `engine/live.py` | `monitor_live()` — open-ended monitoring loop with calibrate-then-replay tone detection. Source-agnostic: tests drive it with `SyntheticCWSource`; `cw_monitor_live` drives it with `SoundDeviceSource`. |
| `engine/stream.py` | `ResultStreamer` — diffs the accumulating `DecodeResult` into JSON batches via an injected sender. Transport-agnostic (the command POSTs them; tests collect them in a list). |
| `engine/simulate.py` | `SimulatedBandSource` — infinite noise + scheduled CW stations (random message/pitch/speed/strength, deterministic per seed). `noise_level`/`paused_signals` are live-mutable; `truth` logs what was transmitted. |
| `engine/bridge.py` | `CWLogBridge` → `QSODraft` (callsign/RST extraction). Framework-agnostic. |
| `engine/export.py` | `DecodeResult` → session dict the monitor animates. |
| `engine/wav.py` | float32 ⇄ WAV bytes (uploads in, downloads out). |
| `services.py` | Engine pass → `CWSession` row. The only Django+engine module. |
| `models.py` | `CWSession` — per-user; telemetry JSON is the replay; audio never stored. |
| `views.py` | Monitor / Live / Decode / Send + `CWSessionCRUDView` (per-user scoped, search-visible to owner only). |
| `consumers.py` / `routing.py` / `api.py` | The live-tape path: `cw_monitor_live --stream` (or `cw_simulate --stream`) → mints an APIToken → POSTs `ResultStreamer` batches to `/cw/live/ingest/` (`api_view` Bearer auth) → channel-layer group `cw-live-<user pk>` → `LiveTapeConsumer` → `/cw/live/` or `/cw/sim/` tab (`monitor.js` live mode follows the data edge). In-memory channel layer = single ASGI process (runserver/daphne); use channels-redis for multi-worker. |
| `models.CWSimControl` / `/cw/sim/control/` | The live knobs (noise, gain, squelch, AFC, static-only). The Simulator AND Live pages POST slider moves (shared include `_receiver_controls.html`); both `cw_simulate` and `cw_monitor_live` poll the row ~2×/s via `services.apply_receiver_controls()` — the DB is the cross-process control channel (dev SECRET_KEY is per-process, so signed tokens wouldn't cross). |
| The responder | `ResultStreamer` meta carries `calls` (callsigns from *completed* words only — a partial trailing word must not spawn a chip). Live/sim pages render them as "Heard on the band" reply chips → `/cw/send/?to=CALL` prefills `{CALL} DE {USERNAME} {USERNAME} K`. |
| `engine/fldigi.py` / `cw_fldigi` | The first real `NetworkTapEngine`: polls fldigi's XML-RPC (`rx.get_data`, stdlib xmlrpc.client, zero deps) and adapts text into `CharEvent`s (wall-clock timestamps, `modem.get_quality` → confidence, CR/LF → word gaps, `morse=""`). `tap_loop()` accumulates into a standard `DecodeResult` so ResultStreamer/save_live_session reuse unchanged. Chars-only modes: `ResultStreamer.tick` falls back to char time when there's no envelope, and `monitor.js` `liveMerge` follows char timestamps for the playhead. Tested against a fake fldigi `SimpleXMLRPCServer`. Streamer meta carries `source` (pill shows `● live · <source>`) + a per-run `feed` id — `monitor.js` flags two ids alternating within 6s as a warn-state interleave conflict. Text-mode chars (`m===""`) draw as a carrier ribbon + commit ticks on the tape. |
| `radiodaemon.py` / `bandscan.py` | The SDR side — RTL dongle enumeration, supervised `rtl_fm` for the FM Radio faceplate, and `rtl_power` band sweeps for the Antenna Survey. Separate from the decode engine: it produces *audio and measurements*, not `CharEvent`s. **Read `docs/skills/sdr-subsystem.md` before touching it.** |
| `rigctl.py` / `transmit.py` / `models.CWRig` | Real rig control: a Django-free Hamlib `rigctld` TCP client (freq/mode/PTT), and the transmit orchestrator — CAT PTT on → ptt_lead_ms → play session audio out the sound device (injected player; sounddevice by default) → PTT off in a finally. One TX at a time (module lock); refuses if PTT already keyed; VOX mode skips PTT. Endpoints `/cw/rig/` (config + probe + tune) and `/cw/rig/tx/`. Tested end-to-end against a fake rigctld TCP server — no Hamlib needed in CI. |
| Rig thumbnails / `static/cw/rigs/` | Model rows show ORIGINAL SVG illustrations by rig archetype (base/compact/mobile/handheld/software), classified from the Hamlib model name in `rigsetup.js` (`rigArchetype`). NO manufacturer photos are shipped (copyright). Operators may drop their own licensed image at `static/cw/rigs/<model_id>.png|webp|jpg`; `_custom_rig_images()` in api.py globs the folder and the row uses the photo instead (broken-image → falls back to text). |
| `rigdaemon.py` / `/cw/rig/setup/` | The rig launcher: serial-port enumeration (stdlib glob, no pyserial), `rigctl -l` catalog parsing (cached), and a supervised `rigctld` subprocess (one per server, argv-validated — int model, existing `/dev/...` device, fixed baud set, never a shell; ring-buffer log). Lifecycle tests run the REAL Hamlib dummy rig and skip if hamlib is absent. Starting via the endpoint saves the choice to `CWRig` and arms the Rig panel + on-air toggle. |
| `qrzlogbook.py` / `/cw/callbook/` | QRZ *logbook* sync (logbook.qrz.com — separate per-logbook API key, not the XML password): FETCH→import (dedupe reused), INSERT per record on export with `qrz_sent_at` marking; duplicate REASONs tolerated. Callbook page = lookup console (reuses `/cw/log/lookup/`), account panel (change/test/unlink XML creds), sync buttons. `settings.QRZ_LOGBOOK_URL` override for the fake-server tests. |
| `fieldcrypto.py` / `eqsl.py` / ADIF import | Credentials (QRZ, eQSL) are Fernet-encrypted at rest via `_CredentialMixin.set_password/get_password`; key file `.cw_credentials_key` (0600, gitignored, `settings.CW_CREDENTIALS_KEY_FILE` override for tests) — NOT SECRET_KEY-derived (dev regenerates it per process). Legacy plaintext upgrades on read; lost key reads as unset, never crashes. `logbook.parse_adif/import_adif`: tolerant parser, dup-skip on (call, UTC minute) so re-imports are no-ops. `eqsl.upload_adif`: credentials embedded as EQSL_USER/EQSL_PSWD header fields, multipart POST, Result-line parsing; `eqsl_sent_at` prevents double-upload. Both tested against fake HTTP servers. NOTE: `api_view` skips JSON parsing for multipart bodies (upstream fix smallstack@229c731) — file-upload endpoints use `request.FILES`. |
| `logbook.py` / `qrz.py` / `models.QSO`+`QRZProfile` | The logbook: `quick_log()` links the session, inherits mode from the session's engine (fldigi:BPSK31 → PSK31), maps freq→band, prefills from worked-before history then QRZ (XML API, session-key cached, re-auth-once on timeout, always degrades to no-enrichment — never breaks logging; base URL swappable via `settings.QRZ_XML_URL` for the fake-server tests). `adif_export()` emits ADIF 3 with UTC dates/times. Keying a reply auto-logs (sendsheet); session pages get `+log` per callsign. LogbookCRUDView: search-registered (owner-visibility), band/mode chip filters (`.order_by()` before `.distinct()` or Meta.ordering duplicates chips — regression-tested), filtered ↓ADIF. |
| `abbrev.py` / `/cw/abbrev/` / tutor mode | CW shorthand → plain English. Python is source of truth (`ABBREVIATIONS` grouped, `LOOKUP` flat, `gloss()` tested); served at `/cw/abbrev/`; the Morse appendix table is generated from it. Tutor toggle (Aa button in `_stage.html`) in `monitor.js` fetches the dict once on first enable and renders a gloss panel of only the abbreviations present in the copy — display-only, honors the Python-decodes/JS-renders rule. |
| `models.CWMacro` / `/cw/macros/` / `static/cw/macros.js` | Message keys: per-user slash macros (defaults seeded on first GET), single GET/POST endpoint (create/update/delete by shape), and the composer UI — keycap chips + slash palette + snippet-style `{placeholder}` fill (known context expands, first unknown is left selected). Popover background uses the layered `linear-gradient(var(--card-bg),var(--card-bg)), var(--body-bg)` recipe so it stays opaque under any palette. |

## Branding & template overrides

The product is **CW Station** (BRAND_* in `config/settings/smallstack.py`).
Assets live in `static/brand/`: `cw-station-text.svg` (topbar wordmark),
`cw-station-icon.svg`, `cw-station-icon.ico` (multi-size, generated with
Pillow — see git history for the generator), `cw-station-social.png`. The
mark is the tape keying **K** (dah·dit·dah, "go ahead") crossing the green
now-line; brand colors #e0b84c amber / #3fb950 green / #7d8590 slate.

Downstream template overrides in `templates/smallstack/includes/`:
- `sidebar.html` — operator-console sidebar: skips the `admin` nav section
  entirely (admin tools live in the staff-gated topbar apps grid and the
  user menu's Admin link, both framework-provided).
- `topbar.html` — sidebar-toggle hamburger wears the brand mark instead of
  the stock green squares.
When pulling from upstream smallstack, diff these two against the originals.
Django `{# #}` comments are single-line — multi-line notes in templates must
use `{% comment %}` or they render as page text.

## Working on it

- **Test loop is synthesize → decode → assert** (`apps/cw/tests/test_engine.py`). Any
  decoder change must keep the blind-bootstrap, speed-sweep, noise-sweep, and
  chunked-equals-oneshot tests green: `uv run pytest apps/cw/`.
- **CLI**: `uv run python manage.py cw_decode --text "..." --wpm 22` (or `--wav`,
  `--session out.json`). Fastest way to eyeball a decoder change. Live sound-card
  monitoring: `cw_monitor_live` (needs `uv sync --extra live` for sounddevice).
- **Sessions must replay**: whatever you change, `session_from_result()` output feeds
  `initCWMonitor()`. Word gaps are stored as `CharEvent(" ")` in `result.chars` — don't
  drop them or copy loses word boundaries (regression-tested).
- **Determinism matters**: synthesized audio (`seed=0` default) must regenerate
  byte-identical WAVs — `services.session_wav_bytes()` relies on it, which is why the
  model stores the *requested* WPM for practice sessions, not the decoded estimate.
- **UI**: templates use only theme variables (read `modern-dark-theme.md` first);
  monitor colors resolve from the palette at init and re-resolve on palette change via
  a MutationObserver in `monitor.js`.

## Adding a new decode mode (the point of the seam)

```python
class MLDecoder(AudioDemodulator):
    name = "ml"
    def process(self, samples: FloatArray) -> list[CharEvent]: ...
    def flush(self) -> list[CharEvent]: ...

manager.add_demodulator(MLDecoder(sample_rate))
```

For an external decoder (fldigi XML-RPC, WSJT-X UDP), subclass `NetworkTapEngine` and
implement `poll()` instead — no audio flows through it. Either way, nothing else in the
app changes.

## Scope guardrails (from `specs/mvp-cw-decode-design.md`)

The MVP deliberately excludes CHIRP (GPL question), Hamlib CAT control, and live
websocket streaming. Engine dependencies are numpy plus `soundfile` (lazily imported
in `audio_io.py`, only for compressed formats); `sounddevice` stays an optional,
lazily-imported extra. Don't add more.

Test fixture: `apps/cw/tests/fixtures/test_de_ab1cd_20wpm_700hz.mp3` — synthesized
CW encoded to MP3 (via `soundfile`), so the compressed-format path is self-verifying.
Long recordings: `session_from_result()` decimates the envelope trace to
`max_env_points` (default 6000) so a 7-minute file doesn't store megabytes of
telemetry — chars and key runs stay exact.
