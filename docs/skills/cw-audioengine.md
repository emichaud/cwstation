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
| `engine/cw.py` | `CWDecoder` — streaming: tone DFT → envelope → adaptive threshold → key runs → elements → chars. Fully adaptive WPM with a two-phase bootstrap. |
| `engine/synth.py` | `synthesize_cw()` — PARIS timing, raised-cosine edges, controllable SNR. The no-hardware test driver AND the transmit path. |
| `engine/audio_io.py` | `load_audio()` — WAV via stdlib, MP3/FLAC/OGG via `soundfile`; `detect_tone()` — spectral peak finder so operators don't guess the pitch. |
| `engine/sources.py` | `ArraySource` / `SyntheticCWSource` / `AudioFileSource` (any format) / `SoundDeviceSource` (guarded optional). |
| `engine/manager.py` | The seam: `AudioDemodulator`, `NetworkTapEngine`, `AudioEngineManager` (fan-out + subscribers). |
| `engine/bridge.py` | `CWLogBridge` → `QSODraft` (callsign/RST extraction). Framework-agnostic. |
| `engine/export.py` | `DecodeResult` → session dict the monitor animates. |
| `engine/wav.py` | float32 ⇄ WAV bytes (uploads in, downloads out). |
| `services.py` | Engine pass → `CWSession` row. The only Django+engine module. |
| `models.py` | `CWSession` — per-user; telemetry JSON is the replay; audio never stored. |
| `views.py` | Monitor / Decode / Send + `CWSessionCRUDView` (per-user scoped, search-visible to owner only). |

## Working on it

- **Test loop is synthesize → decode → assert** (`apps/cw/tests/test_engine.py`). Any
  decoder change must keep the blind-bootstrap, speed-sweep, noise-sweep, and
  chunked-equals-oneshot tests green: `uv run pytest apps/cw/`.
- **CLI**: `uv run python manage.py cw_decode --text "..." --wpm 22` (or `--wav`,
  `--session out.json`). Fastest way to eyeball a decoder change.
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
