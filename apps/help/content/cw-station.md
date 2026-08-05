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

Radio-side tips: tune the signal so its note sits at one steady pitch (the narrow CW
filter helps), and prefer slow AGC — the decoder rides through fading, but heavily
pumped audio is hard for anything to copy.

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
