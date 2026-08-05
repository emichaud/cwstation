# CW Station — Operator's Guide

The CW Station decodes and sends Morse through your sound card — no CAT control, no
special hardware. Three pages do the work: **Monitor** (the paper tape), **Decode**,
and **Send**. Every pass is saved as a session you can replay.

## Decoding off the air

1. Record the signal — from your receiver's audio out into your computer, or export
   from SDR software (GQRX, SDR++, etc.). **WAV, MP3, FLAC, and OGG all work**, mono
   or stereo — W1AW code-practice MP3s decode as-is.
2. Open **Decode → Off the air** and pick the file. Leave **Auto-detect tone** on —
   the station finds the CW note from the spectrum. (Or untick it and set the pitch
   yourself, commonly 500–800 Hz.)
3. Decode. You get the copy with per-character confidence (shaky characters show in
   red), the sender's speed, and any callsigns spotted in the copy. Open the session
   to replay it on the tape.

**Tips from the bench:**

- The decoder is fully adaptive on speed — you never set WPM for receive. It buffers
  the opening marks, works out the dit length, then replays them, so the first
  character isn't lost.
- If the copy is empty or garbage with auto-detect off, the tone is almost always
  wrong — turn auto-detect back on, or set it to the pitch you actually hear.
- Like every threshold decoder, it's excellent on clean or machine-sent CW and
  degrades on weak, fading, or irregular hand keying — a trained ear still wins
  there. The confidence shading tells you which characters to distrust.

## Practice mode

**Decode → Practice** synthesizes a message at a speed and noise level you choose,
then decodes it back. Two honest uses:

- **Code practice** — generate clean CW at your target speed, play the session audio,
  copy by ear, then check yourself against the ground truth.
- **Seeing the decoder work** — add noise (try SNR 20 → 12 → 6 dB) and watch on the
  tape how the adaptive threshold holds up, and where it starts dropping elements.

Practice sessions store both the ground truth and the decode, and show a character
accuracy score.

## Message keys (macros)

The Send composer has a keyer memory bank — the CW equivalent of contest-logger
F-keys. Type `/` in the message box and a command palette opens: filter, arrow keys,
Enter to insert. Or click a keycap chip below the composer. Placeholders expand at
insert time — `{mycall}` fills with your call, `{call}` with the station you're
replying to, `{rst}` defaults to 599, and anything the app doesn't know is left
*selected* in the composer so you type straight over it.

You start with the standard set (`/cq`, `/qrz`, `/rst`, `/73`, `/agn`, `/qth`); edit
them or add your own in the **Message keys** card next to the composer — changes save
on blur, and your bank is yours alone.

The same message keys live on the **Live** and **Simulator** pages as a send sheet:
a docked bar at the bottom of the tape slides up like a phone keyboard when you need
to key something. Click a "Heard on the band" reply chip and the sheet opens
pre-addressed to that station; hit **Key it** and the message is keyed and played
back inline without ever leaving the tape.

## Sending

**Send** turns text into properly timed CW audio: PARIS timing (dit = 1200 ms ÷ WPM)
with raised-cosine keying edges, so there are no key clicks. Prosigns are first-class —
use the palette buttons or type `<AR>`, `<SK>`, `<BT>`, `<KN>`, `<AS>` inline and they
key as single run-together symbols.

Play the audio in the browser or download the WAV. To put it on the air, feed it to
your rig's audio input like any digital mode:

- Use your data/soundcard interface and drive levels you already trust for FT8/PSK —
  keep ALC near zero.
- VOX or manual PTT; remember it's 100% duty cycle while keying, like any CW.
- Check the band plan and your license privileges — machine-generated CW is still CW.

## Sessions and the Monitor

Every decode and send is a **session**: the text, speed, tone, SNR, callsigns heard,
and the full replay telemetry. The **Monitor** replays them as a paper tape — keyed
dits and dahs scroll past the *now* line, and each letter resolves above its elements
at the exact moment the decoder committed it. The envelope trace behind the bars shows
what the adaptive threshold was working with.

The tape has a **sidetone toggle** (the speaker button) — turn it on and you hear the
keying exactly as the tape plays it, at the session's tone, following the playback
speed control. For sent messages, the readout shows your whole message and **colors
each character in as it completes** — dim before it's keyed, glowing while on the
air, accent-colored once sent.

Sessions are searchable (the topbar search finds text in your copy) and private to
your account. Audio for synthesized sessions is regenerated on demand; uploaded
recordings are not stored — only their decode.

## Live monitoring with a radio attached

Plug your radio's audio out (headphone/line out, a data interface like a DigiRig or
SignaLink, or your rig's USB audio codec) into a sound-card input, install the live
extra once (`uv sync --extra live`), then:

```bash
# see your capture devices, pick the one the radio is on
uv run python manage.py cw_monitor_live --list-devices

# monitor — the CW tone is auto-detected from the first seconds of audio
uv run python manage.py cw_monitor_live --device 2

# fixed tone + WPM prior, and save the run as a replayable session on exit
uv run python manage.py cw_monitor_live --tone 700 --wpm 25 --save yourusername
```

Decoded characters stream to the terminal in real time (a character resolves a
fraction of a second after the sender's keying stops). Ctrl-C stops the monitor,
prints a summary, and — with `--save` — stores the whole run as a session you can
replay on the tape, sidetone and all.

### The live tape in the browser

Add `--stream yourusername` (plus `--server http://host:port` if the web app isn't on
the default port) and open **CW Monitor → Live**: the paper tape scrolls in real time
as signals are decoded — keying bars, envelope, WPM/SNR gauges, and the sidetone
toggle all work exactly like a replay, except the *now* line is actually now.

```bash
uv run python manage.py cw_monitor_live --stream yourusername \
    --server http://localhost:8005 --save yourusername
```

The command mints its own short-lived API token for the stream and revokes it on
exit; the Live page shows the exact command to run, pre-filled for your account. The
status pill on the page tells you when the feed is connected.

The Live page has the same **receiver controls** as the Simulator — input gain,
squelch, and AFC steer the running monitor within half a second. And when the decoder
identifies a station, its callsign appears under the tape in **Heard on the band**:
click **reply** and the Send composer opens pre-filled with a standard answer
(`W1AW DE YOU YOU K`) ready to key into audio.

Radio-side tips: tune the signal so its note sits at one steady pitch (the narrow CW
filter helps), and prefer slow AGC — the decoder rides through fading, but heavily
pumped audio is hard for anything to copy.

## Rig control (Hamlib) — transmit for real

With Hamlib's `rigctld` running on the machine wired to the radio, the station gets
real CAT control. Install Hamlib (`brew install hamlib` / `apt install hamlib-utils`)
and start the daemon:

```bash
rigctld -m 1                        # dummy rig — test everything with no radio
rigctld -m 3085 -r /dev/ttyUSB0     # a real rig, e.g. IC-7300 (rigctl -l lists models)
```

Then open the **Rig** panel on the Live page: expand *rigctld setup*, tick
**enabled**, and the panel shows the dial — frequency, mode, and an ON AIR indicator,
refreshed every few seconds. **Tune** sets the frequency; **CW mode** puts the rig in
CW.

To transmit, tick **on air** in the send sheet before *Key it*. The sequence is:
CAT PTT on → a short lead delay (so the first dit isn't clipped) → the keyed audio
plays out the configured sound device into the rig — the same audio path as any
soundcard digital mode — → PTT off, *always*, even if playback fails. Untick
**CAT PTT** in the setup to rely on the rig's VOX instead. TX audio playback needs
the live extra (`uv sync --extra live`) and the sound device that feeds your rig
(set it in the panel; blank = system default).

On-air etiquette still applies: drive levels low (ALC near zero), check the
frequency is clear, identify per your license. One message transmits at a time —
the app refuses to key if PTT is already down.

## The band simulator — no radio required

**CW Monitor → Simulator** is a practice band: continuous radio static with stations
hidden in it, calling at random pitches (450–950 Hz), speeds (14–26 WPM), and signal
strengths. Start it from a terminal and it streams to the page's live tape:

```bash
make sim                              # streams for the admin user, saves the run
SIM_USER=you PORT=8010 make sim       # your user, matching your dev server's port
# or the full command:
uv run python manage.py cw_simulate --stream yourusername --save yourusername
```

Two things make it a real workbench rather than a demo:

- **AFC (automatic frequency control)** — the decoder chases the strongest carrier in
  the band, re-tuning itself to each new station. Watch the Tone gauge re-lock as
  stations appear at different pitches. AFC only re-tunes between marks and only for a
  clearly prominent peak, so it doesn't chase noise. Toggle it off and the decoder
  stays parked at one pitch — off-frequency stations degrade to garble.
- **Receiver controls, live** — the page's knobs steer the *running* simulation within
  half a second: **band static** (raise it and watch false characters appear),
  **input gain**, and **squelch** — an SNR gate below which the key can't open. The
  classic exercise: turn static up until junk characters appear, then bring squelch up
  until the junk stops without losing real copy. "Static only" turns off the stations
  entirely — a pure false-positive test bench.

With `--save`, stopping the simulation stores the run as a session scored against
what the stations actually transmitted — your copy accuracy under those settings.
The tape's sidetone toggle lets you listen to the band while you work the knobs.

## Command line

The same engine runs headless:

```bash
# sanity check, no radio needed
uv run python manage.py cw_decode --text "CQ CQ DE N0CALL K" --wpm 22

# decode a recording (WAV/MP3/FLAC/OGG; tone auto-detected unless --tone given)
uv run python manage.py cw_decode --wav 220111_20WPM.mp3

# watch the QSO-draft bridge extract callsigns/RST
uv run python manage.py cw_decode --text "CQ TEST DE W1AW 599" --log
```

## What's next

The engine is built as a seam: fldigi/WSJT-X taps, an ML decoder for weak signals,
and a proper QSO logbook all plug into the same event stream without replumbing.
See `specs/mvp-cw-decode-design.md` in the repo for the roadmap.
