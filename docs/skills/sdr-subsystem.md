# Skill: The SDR subsystem (radiodaemon / bandscan)

**Read this before touching `apps/cw/radiodaemon.py`, `apps/cw/bandscan.py`, the
FM Radio page, or the Antenna Survey page.** It captures the rules that were
learned by getting them wrong — each one below cost a real debugging session.

Operator-facing documentation lives in the help system
(`apps/help/content/sdr-hardware.md`, `fm-radio.md`, `antenna-survey.md`); this
file is the implementation contract.

## The map

| Module | Role |
|---|---|
| `radiodaemon.py` | Supervises one `rtl_fm` for FM listening. Device enumeration, tune/stop/retune/seek, the pidfile reaper, the injected audio sink. |
| `bandscan.py` | Sweeps bands with `rtl_power` and scores them. The band table, `summarize()`, the worker thread. |
| `models.RadioStation` | FM presets, per user. |
| `models.AntennaSurvey` | A scored sweep, labelled by antenna **and by device and gain**. |
| `api.radio_control` / `api.band_survey` | The two endpoints. Both `@api_view(require_auth=True)`. |
| `static/cw/radio.js` + `radio.css` | The tuner faceplate. |
| `static/cw/survey.js` + `survey.css` | The antenna bench. |

## The hard rules

### 1. The dongle is exclusive — serialise everything

One process can hold the device. `radiodaemon._op_lock` wraps *whole operations*
(tune, stop, seek), not individual steps. `retune()` exists because a stop
followed by a start as two separate calls can interleave with another request and
leave an untracked `rtl_fm` playing forever.

`bandscan.start()` **refuses** when the receiver is playing rather than stopping
it — a survey takes ~20 s and silently killing someone's audio is worse than an
error message.

### 2. A spawned child outlives the dev server — hence the pidfile

The autoreloader restarts Django while `rtl_fm` keeps running; the fresh process
knows nothing about it, so Stop stops nothing and static keeps playing. This was
a real bug report. `_reap_stale()` reads `_PIDFILE`, **verifies the PID's command
is actually `rtl_fm`** (PIDs recycle), and checks it isn't the child we're
currently supervising (the first draft killed our own process on a double-start).

If you add another long-lived subprocess here, give it the same treatment.

### 3. The two tools spell direct sampling differently

```
rtl_fm    -E direct2      # a value
rtl_power -D              # a bare flag
```

They are **not** interchangeable, and both were verified against the installed
binaries. Getting this crossed produces noise that looks like a bad antenna.
`test_sweep_argv_carries_device_and_direct_sampling` pins it.

Direct sampling is applied **per band** (`Band.hf`), because using it on a VHF
band just adds noise.

### 4. `-M wbfm` is a preset — don't add `-s`

`wbfm` expands to `-s 170k -r 32k -E deemp -A fast …`. Passing your own `-s`
overrides the *input* rate the wideband demodulator needs and garbles the audio.
The output rate is `AUDIO_RATE` (32 kHz), which is what the sink is opened at.

### 5. stdout is PCM, so the process is binary — decode stderr

`rtl_fm`'s stdout carries audio, so `Popen` runs without `text=True`. That makes
stderr `bytes`, which is not JSON-serialisable and blew up the status endpoint
with a 500. `_log()` decodes defensively.

### 6. Never cache an empty device scan

`rtl_test` claims the device to probe it, so a scan racing a still-exiting
`rtl_fm` returns nothing. Caching that strands the page on "No SDR detected"
until a manual rescan — the one false alarm this UI must not raise. Only
successful scans are cached; `refresh=1` forces a re-probe (both pages have a
rescan control).

### 7. Measurements are only comparable within one device and one gain

The antenna survey is a *measurement tool*, so its numbers have to be honest:

- Gain is **snapped to the tuner's real steps** (`nearest_gain`) and stored, so a
  recorded gain is a gain that was used. `rtl_power` would round silently.
- The **device** is stored on every survey. Two dongles measure two front ends;
  the comparison table flags a mismatch rather than implying an antenna result.
- SNR (peak over median floor) is the metric, **not** absolute power — a bigger
  antenna raises the noise floor too.
- Bands carry a `reference` flag for always-on transmitters (FM, NOAA, 10 m
  beacons, WWV). A quiet ham band measures propagation, not hardware, and the UI
  says so. If you add a band, set `reference` only if it truly never stops.

### 8. Only RTL sticks are detected

Everything goes through `rtl_test`/`rtl_fm`/`rtl_power`. SDRplay, Airspy and
HackRF speak different drivers and are invisible — the empty state says this
explicitly rather than implying the hardware is broken. Supporting them means
SoapySDR, which is not a dependency today.

## Working on it

```bash
make devices                  # what the machine sees
uv run pytest apps/cw/tests/test_radio.py apps/cw/tests/test_bandscan.py
```

Hardware tests skip cleanly with no dongle (`needs_dongle`). Audio never reaches a
sound card in tests — `start(sink=…)` takes a fake, the same seam `transmit.py`
uses for the PTT sequence.

**Verify against real hardware before claiming a fix.** Every bug listed above
passed the unit tests first. `rtl_power` measurements are the ground truth: a
band with real signal shows a peak tens of dB over the floor; a dead one sits
within a couple of dB.

## UI gotchas (both pages)

- `cw.css` styles **every** console range input and **every** `.cw-field label`
  at high specificity. A custom slider thumb or a `<label>`-based card needs a
  selector that outranks `body.cw-console …`, or it silently loses. Both bit us.
- `display: flex` **beats** the `[hidden]` attribute. Any flex element you hide
  with `hidden` needs an explicit `[hidden] { display: none }`.
- `api_error()` replies `{"errors": {"__all__": ["msg"]}}` — not `{"error": …}`.
  Reading the wrong key silently shows a generic message where the server sent a
  specific one.
- The seven-segment display font (`DSEG7`, OFL, bundled in `static/cw/fonts/`)
  renders `!` as a blank cell — used to pad `88.1` so it sits where `108.1` would.
  Anything set in that family shows digits only; units must not inherit it.
