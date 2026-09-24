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
  beacons, WWV, UHF TV). A quiet ham band measures propagation, not hardware,
  and the UI says so. If you add a band, set `reference` only if it truly never
  stops.

### 7a. A wide band needs `dwell_s` and `floor_pct` — the defaults are for narrow ones

Both defaults on `Band` are tuned for the ≤20 MHz bands that came first. A wide
band (UHF TV is 138 MHz) breaks them in ways that look exactly like a bad antenna:

- **`dwell_s`.** `rtl_power` splits one `-i` interval across every ~2.4 MHz hop
  in the range, and its own help says it is "buggy if a full sweep takes longer
  than the interval". 138 MHz is ~58 hops; on the 2 s default each hop gets
  ~34 ms, which is retune overhead, not a measurement. UHF TV uses 12 s.
  `test_wide_bands_dwell_long_enough_to_hop_the_range` pins ≥100 ms a hop.
- **`floor_pct`.** `summarize()` scores peak-over-floor, and the floor is the
  median. That assumes the band is mostly empty. UHF TV is contiguous 6 MHz
  channels, so in a strong market most bins *are* signal — the median lands
  inside a haystack and the loudest thing the stick can hear scores "nothing
  heard". UHF TV uses the 20th percentile.

`floor_pct == 50` takes `statistics.median` **verbatim**, not an equivalent
percentile — stored `AntennaSurvey` rows were scored that way and a fraction of
a dB of drift would silently invalidate comparison against them. Don't
"simplify" that branch away; `test_the_median_floor_is_unchanged_for_every_other_band`
guards it.

Also: the survey page's time estimate sums `dwell_s` (it used to multiply a flat
constant by the chip count), and the **instant check skips bands slower than
`QUICK_DWELL_S`** — always-on isn't enough to earn a place in a button called
instant.

### 7b. Tuned is not playing — and PortAudio's device list goes stale

A real report: the faceplate said playing, the dongle was tuned, and there was
no sound. The log had it — `audio sink stopped: Error opening RawOutputStream:
Internal PortAudio error [PaErrorCode -9986]`.

**PortAudio enumerates the sound devices once, when it initialises, and holds
that list for the life of the process.** A dev server that has been up for days
— across sleeps, headphones, a call grabbing the default output — then fails to
open a stream, while a freshly started process opens the very same speakers
fine. That asymmetry is the fingerprint; if a restart "fixes" the audio, this
was it.

Three things follow, all in `radiodaemon.py`:

- `_speaker_sink` retries once plainly, then re-initialises PortAudio
  (`sd._terminate()` / `sd._initialize()`) to refresh the list and tries again.
  That reset is **process-global** — it would disturb a live-monitor capture or
  TX sidetone stream — so it only happens after a plain retry has also failed,
  by which point there is no audio to protect.
- The sink opens on the **pump thread**, so `start()` can't see the failure
  inline. `_sink_open` / `_sink_gone` bridge that: a start that never gets
  audio stops `rtl_fm` and raises, rather than leaving it blocked on a full
  pipe holding the exclusive dongle.
- `status()` reports **`audio`** separately from `running`, and `radio.js` says
  so while running. The old poll only reported errors once the process died —
  and in this failure the process is perfectly healthy, which is precisely why
  the one mode that produces silence was the one the UI couldn't show.

### 7c. `_reap_stale()` reaps across processes — mind the second server

The pidfile is global to the machine, and `_reap_stale()` cannot distinguish a
live receiver owned by *another* process from an orphan. So **any second
process that starts a receiver kills the first one's** — a `manage.py shell`, a
pytest run, a second `runserver` someone left up. That's by design (it's what
makes Stop work across an autoreload), but it means a stray dev server turns
into mysterious mid-song deaths. Check `lsof -nP -iTCP:8010 -sTCP:LISTEN`
against `ps` before blaming the code.

Relatedly, `list_devices()` skips probing when `_foreign_rtl_fm()` sees one.
Probing wouldn't hurt the receiver — `rtl_test` just fails to claim the
interface — but it returns `[]`, and an empty scan is the false "No SDR
detected" rule 6 exists to prevent.

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
