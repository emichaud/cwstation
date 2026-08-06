# CW Station — Operator's Guide

The CW Station decodes and sends Morse through your sound card — no special
hardware required. Three pages do the work: **Monitor** (the paper tape),
**Decode**, and **Send**. Every pass is saved as a session you can replay.

New here? Start with the [Tutorial](/smallstack/help/cw-tutorial/) — ten minutes,
step by step. Character tables live in the
[Morse Code Reference](/smallstack/help/morse-reference/).

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

With a rig connected (or the dummy rig from Rig Setup), the tape's gauge cluster
gains an **RF readout**: the dial read over CAT, corrected by the audio tone for the
current mode (USB: dial + tone; LSB: dial − tone; CW: the dial already reads signal
RF) — so as AFC re-locks between stations, you see each one's actual frequency. The
Simulator shows the same gauge against a **virtual VFO** parked at 14.0500 USB, so
simulated stations spread across a pretend band segment just like real ones.

The Live page has the same **receiver controls** as the Simulator — input gain,
squelch, and AFC steer the running monitor within half a second. And when the decoder
identifies a station, its callsign appears under the tape in **Heard on the band**:
click **reply** and the Send composer opens pre-filled with a standard answer
(`W1AW DE YOU YOU K`) ready to key into audio.

Radio-side tips: tune the signal so its note sits at one steady pitch (the narrow CW
filter helps), and prefer slow AGC — the decoder rides through fading, but heavily
pumped audio is hard for anything to copy.

## The logbook

The station logs contacts the way it does everything else — quietly, from the flow:

- **Keying a reply logs it.** Answer a "Heard on the band" chip and hit *Key it* —
  the QSO is logged automatically (the sent-log row grows a *logged* link, with a
  `worked ×N` note if you've had them before).
- **`+log` on any session.** Every callsign badge on a session page has a small
  `+log` action; the QSO links back to that session's tape.
- **Smart prefill.** A logged QSO inherits its session's mode (a PSK31 tap session
  logs as PSK31), takes the rig's frequency when one is connected (band derived
  automatically — 14.055 → 20m), and fills name/QTH/grid/country from your own
  history with that call, or from **QRZ.com** if you've linked an XML subscription
  in the Callbook card (every call links to its QRZ page regardless, no account
  needed).
- **ADIF export & import.** The **↓ ADIF** button downloads the log — respecting
  whatever search/band/mode filter you're looking at — with dates and times in UTC
  per the ADIF spec. **↑ Import** brings an existing log in (any ADIF file);
  duplicates (same call, same UTC minute) are skipped, so re-imports are safe.
- **eQSL.cc upload.** Save your eQSL credentials in the card at the bottom of the
  Logbook and the **↥ eQSL** button appears: it uploads QSOs not yet sent
  (respecting the current filters) and marks them, so nothing double-uploads.
- **Credentials are encrypted at rest.** QRZ and eQSL passwords are stored
  Fernet-encrypted with a key held in `.cw_credentials_key` next to the project
  (created automatically, permissions 0600, gitignored) — not in the database, and
  never echoed by any API response.
- **Search.** QSOs and sessions are both in the global search (⌘K): a callsign,
  a name, a QTH, or words from your copy all find their records — your own only.
  On the Logbook page, band and mode chips narrow the list one click at a time.

## PSK31 and other digital modes — the fldigi tap

The station isn't CW-only: if you run **fldigi** (the standard sound-card
digital-modes program), its decoded text — PSK31, RTTY, Olivia, whatever modem it's
set to — flows onto the same live tape:

```bash
# fldigi running with XML-RPC enabled (Configure → Misc → XML-RPC, default port 7362)
uv run python manage.py cw_fldigi --stream yourusername --save yourusername
```

fldigi does the demodulating (it's best-in-class at it); the tap adapts its text into
the station's event stream. Everything downstream just works: the decode window fills
in real time, callsigns spotted in PSK31 copy become **Heard on the band** reply
chips, `--save` stores the copy as a session, and with a rig connected the RF gauge
reads dial + fldigi's carrier.

In text modes the tape draws a **carrier ribbon** — a continuous bar (PSK31's carrier
never stops) with a bright tick at each character commit and the glyph above it, so
traffic is visible on the tape even without on-off keying.

**One feed at a time per tape.** The live pill names whichever command is feeding it
(`● live · simulator`, `· sound card`, `· fldigi BPSK31`) — and if two streamers run
at once, it flips to a red **⚠ two feeds interleaving — stop one** warning, because
interleaved streams scramble the copy.

## Rig control (Hamlib) — transmit for real

**Do you need Hamlib? No — not until a real radio is on the desk.** Everything else
in the station (decode, practice, send, the simulator, the live tape fed by
recordings) works with no Hamlib installed. Skip this whole section until you have a
rig to control; nothing you learn elsewhere changes when you come back.

When that day comes: with Hamlib's `rigctld` daemon running on the machine wired to
the radio, the station gets real CAT control — frequency, mode, and PTT for ~250 rig
models. Install Hamlib (`brew install hamlib` / `apt install hamlib-utils`) and start
the daemon:

```bash
rigctld -m 1                        # dummy rig — test everything with no radio
rigctld -m 3085 -r /dev/ttyUSB0     # a real rig, e.g. IC-7300 (rigctl -l lists models)
```

**Try it before you own a radio**: `rigctld -m 1` is Hamlib's built-in dummy rig — a
software radio that answers every CAT command. Enable the rig in the panel (host
`127.0.0.1`, port `4532`), and you can tune it, switch modes, and watch the panel
track it — the exact same code path a real rig uses. This station's rig client is
verified against both a protocol test double and genuine Hamlib via this dummy rig.

**The easy way — the Rig Setup launcher** (CW Monitor → Rig Setup): it scans the
machine's USB-serial ports (with chip hints so you can tell the radio from an
Arduino), lets you search Hamlib's catalog for your rig model — the "pick the right
modem driver" step — choose a baud, and starts and supervises `rigctld` for you. The
daemon log shows the exact command line, and the Verify module reads the dial over
CAT the moment the link is up. **Test with dummy rig** runs the whole path with no
hardware. Starting the daemon also arms the Rig panel and the send sheet's on-air
toggle automatically.

Or by hand: open the **Rig** panel on the Live page, expand *rigctld setup*, tick
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
