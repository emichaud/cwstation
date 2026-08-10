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
F-keys. A **message key** is a whole message template with a `/name`. Type `/` in
the message box and a command palette opens: filter, arrow keys, Enter to insert.
Or click a keycap chip below the composer.

You start with the standard set (`/cq`, `/qrz`, `/rst`, `/73`, `/agn`, `/qth`); edit
them or add your own in the **Message keys** card next to the composer — changes save
on blur, and your bank is yours alone. Example:

```
/cq   →   CQ CQ CQ DE {mycall} {mycall} K
```

### Placeholders — the `{tags}`

Anything in `{curly braces}` is a placeholder that fills in automatically. There are
two kinds:

**Built-in, filled by the station** — you never define these:

| Tag | Fills with |
|-----|-----------|
| `{mycall}` | your station callsign (set it below; falls back to your username) |
| `{call}` | the station you're replying to (from a "Heard on the band" chip or `?to=` link) |
| `{rst}` | the signal report — defaults to `599` |

**Your own custom tags** — reusable values you define once (see below). Anything the
app still doesn't recognise is left *selected* in the composer so you type straight
over it.

### Custom tags — define `{yourtag}` once, reuse everywhere

A custom tag is a single named value — where a message key is a whole message, a tag
is one word you drop into many messages. Open the **Custom tags** card under the
composer and add a row:

```
{rig}   =   KW4420
{ant}   =   DIPOLE
{qth}   =   BOSTON MA
```

Now write the tag in any message — in a message key like `/qth → QTH IS {qth} {qth} BK`,
or straight into the composer — and it expands to your value. Expansion happens **both**
when you insert a message key **and** when you press **Key it**, so a tag typed by hand
into the composed message still resolves before it's sent. Tags are lowercase letters,
digits, and dashes; you can't reuse the reserved names `mycall`, `call`, or `rst`.

### Your callsign

`{mycall}` and the ADIF `STATION_CALLSIGN` both come from your **station callsign**.
Set it once — the "Your callsign" box sits on both the **Send Setup** page and the
**Rig Setup** page. Leave it blank and it falls back to your login username. It's
stored per operator, so a shared install just works.

## Where you set up vs. where you send

Sending is split into a setup page and two places you actually key — because you
usually only key when you're *listening* (to reply) or when you want a *WAV file*:

- **Send Setup** (`/send`) is settings, not a keyer. Set your default speed and
  sidetone (with a "Hear my defaults" preview), your callsign, your message keys, and
  your custom tags. Everything else starts from what you set here.
- The **send sheet** on **Live** and **Simulator** is the primary keyer — it goes out
  to the radio. A docked bar at the bottom of the tape slides up like a phone keyboard;
  it starts at your default speed/sidetone, has your message keys, tag chips, and
  prosign buttons, and an "on air" toggle when a rig is connected. Click a "Heard on
  the band" reply chip and it opens pre-addressed to that station.
- The **keyer on the Decode page** is the same composer, tuned for making a **WAV
  file** — type a message (message keys, tag chips, and prosigns all work), pick a
  speed, and **Make WAV** to play it and download it. **Decode it →** synthesizes and
  copies it back so you can check it.

## Sending

Keying turns text into properly timed CW audio: PARIS timing (dit = 1200 ms ÷ WPM)
with raised-cosine keying edges, so there are no key clicks. Prosigns are first-class —
use the buttons or type `<AR>`, `<SK>`, `<BT>`, `<KN>`, `<AS>` inline and they
key as single run-together symbols.

Play the audio in the browser or download the WAV (the Decode keyer). To put it on the
air, use the send sheet's **on air** toggle, or feed a WAV to your rig's audio input
like any digital mode:

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

### Tutor mode

Learning the shorthand? Hit the **🎓 Tutor** button in the controls under the tape.
A *plain English* panel appears and expands the CW shorthand in your copy as it comes
in — `CQ` → "calling any station", `QTH` → "location", `73` → "best regards", and so
on. It only glosses what's actually on the tape, so it teaches in context without
cluttering the copy; toggle it off with the same button.

Tutor mode also covers **prosigns** (the run-together symbols like `<AR>` end of
message, `<SK>` end of contact, `<BT>` pause) and Q-signals. Hover any glossed term
on the tape for the full meaning. The complete list — every abbreviation, Q-code, and
prosign the station knows — is in the [Morse Code Reference](/smallstack/help/morse-reference/),
and you can teach it your own expansions from the shorthand table there.

Tutor mode is available on every tape: **Live**, **Simulator**, the **Monitor**
replay, and any saved **session**.

Radio-side tips: tune the signal so its note sits at one steady pitch (the narrow CW
filter helps), and prefer slow AGC — the decoder rides through fading, but heavily
pumped audio is hard for anything to copy.

## Receiving CW with an RTL-SDR

A cheap **RTL-SDR** dongle (Nooelec NESDR, RTL-SDR Blog V3, and the like) makes a fine
receive-only front end for CW Station. A few things to understand first, because they
save a lot of confusion:

- **It receives, it never transmits.** You can decode and monitor CW with it, but
  **Send** won't work through it — keying needs a real transmitter. This is a property
  of the hardware, not a setting.
- **It is not a CAT rig.** An RTL-SDR presents a raw USB device, not a serial port, so
  it will **never appear in Rig Setup** — and that's correct, not a fault. Rig Setup
  (Hamlib) is for transceivers with a CAT serial port. The SDR reaches CW Station as an
  **audio source** instead, exactly like a sound card (see *Live monitoring* above).
- **Mind the frequency range.** A stock dongle tunes roughly **25 MHz – 1.7 GHz**, which
  *misses* the busy HF CW bands (40 m / 20 m, below 25 MHz). Natively you can still copy
  CW on **10 m (28 MHz), 6 m (50 MHz), and 2 m (144.0–144.1 MHz)** when the band is open.
  To reach the HF bands where CW actually lives, add an **upconverter** (e.g. a Ham It Up
  / SpyVerter) or use a dongle with **direct-sampling HF** built in (the RTL-SDR Blog V3
  has it; the NESDR SMArt does not).

### A one-box alternative — skip the dongle + upconverter

If you'd rather buy one thing that covers HF *and* VHF/UHF natively — no upconverter, and
a better receiver than an RTL dongle — a **wideband SDR receiver** does exactly that. All
of these are still receive-only and reach CW Station the same way (audio in):

| Device | Coverage in one box | Notes |
|---|---|---|
| **SDRplay RSP1B / RSPdx** ⭐ | ~1 kHz – 2 GHz | Best all-in-one. 14-bit ADC → far more dynamic range than the RTL's 8-bit, which matters on crowded HF. RSPdx has a dedicated HF path; RSP1B is the value pick. |
| **RTL-SDR Blog V3** | HF (direct-sampling ≤24 MHz) + 24 MHz – 1.7 GHz | Cheapest "both in one," but HF is 8-bit and unfiltered — usable, not great. |
| **Airspy HF+ Discovery** | 0.5 kHz – 31 MHz + 60 – 260 MHz | Superb HF/CW receiver, but **no 70 cm** and a VHF gap — not truly all-band. |

For most people the **SDRplay RSP1B** is the sweet spot: one box, full HF through UHF,
and it drives GQRX/SDR Console/CubicSDR just like the dongle — so nothing below changes.

### If you also want to *transmit* CW

Every SDR above is receive-only. To key CW on the air you need an actual **transceiver**,
which is a different category — and unlike an SDR, a transceiver with a CAT serial port
**does** show up in **Rig Setup**, so CW Station can read its dial and key PTT (see *Rig
control* below). Options, small to large:

- **QRP-Labs QMX** or **(tr)uSDX** — tiny, inexpensive multi-band CW/digital transceivers;
  great for a receive-*and*-send bench next to CW Station.
- **A full HF rig** (e.g. Icom **IC-7300**, Yaesu, Kenwood) — connect its USB/CAT and audio
  and you get real receive, transmit, and CAT rig control in one radio.

With a transceiver you don't need the SDR at all — its own receiver feeds the decoder and
its transmitter keys what you Send. The SDR route above is for **listening**; the rig route
is for **working stations**.

### What about a Baofeng (or any FM handheld)?

A common question — and the honest answer is *not for real CW*, for two independent
reasons, but there's one thing it can do:

- **No CAT control.** This Hamlib knows 312 rigs and **zero Baofengs** — a UV-5R has no
  live CAT interface (its cable is a **CHIRP memory-programming** cable, not rig control),
  so it will **never appear in Rig Setup**.
- **FM-only, wrong bands.** A Baofeng does **2 m / 70 cm FM**; real CW is an SSB/CW-mode
  signal, almost all on HF. Point FM at CW and you get clicks, not the steady note the
  decoder needs — so it can't copy on-air CW.

The one thing that *does* work is **MCW** (Modulated CW — a CW tone sent as audio over an
FM channel), because CW Station works on audio:

- **Transmit:** feed CW Station's keyed **Send** audio into the handheld's **mic** (cable
  or even acoustically) and key it with **VOX** or manual PTT — it goes out as an FM tone
  on 2 m/70 cm simplex.
- **Receive:** run the receiving radio's **speaker/audio-out into your sound card** → the
  **Decode** page or live monitor copies the tone off the FM audio.

So a $25 handheld makes a fun over-the-air **MCW** practice loop — just know it's
audio-coupled MCW over FM, **not** CAT and **not** HF CW. For actual CW, use the SDR
(listen) or transceiver (work stations) routes above.

### Choosing a radio for CW

Cutting through all of the above — three questions, in order. The first is the one that
trips most people up, because a radio can *cover* the CW frequencies and still be useless
for CW.

1. **Can it demodulate CW at all?** It must have an **SSB or CW mode** (a BFO / product
   detector). If the modes are only **AM / FM / WFM**, it *cannot* turn a CW signal into
   the steady tone the decoder needs — no matter how much of the band it "receives."
   This is what rules out the RTL-SDR-in-FM, the Baofeng, and broad-RX handhelds like the
   Radtel whose HF reception is AM-only.
2. **Does it cover the bands you want?** Most CW lives on **HF (40 m / 20 m)**. A stock
   RTL-SDR (25 MHz+) and a 10 m-only rig both miss it; an all-HF radio or an SDR with HF
   coverage reaches it.
3. **Do you want Rig Setup (CAT) niceties?** Optional. CAT gives auto **dial-readout** and
   **PTT** in CW Station, but decode (audio in) and send (audio + VOX) work fine without
   it. Only transceivers with a **Hamlib backend** appear in Rig Setup — cheap handhelds
   and export radios generally don't.

| Radio | Copy CW? | Send CW? | Rig Setup (CAT)? | Catch |
|---|---|---|---|---|
| **RTL-SDR dongle** | ✅ in SSB/CW app mode | ❌ RX only | ❌ never | No HF without upconverter/direct-sampling |
| **Wideband SDR** (SDRplay RSP1B) | ✅ | ❌ RX only | ❌ | Receive-only; best one-box listener |
| **Baofeng / FM handheld** | ❌ FM-only | ⚠️ MCW-over-FM only | ❌ | Not a CW radio |
| **Radtel RT-950-class** | ❌ if AM/FM-only¹ | ⚠️ MCW-over-FM | ❌ | Broad RX ≠ CW-capable — check for SSB |
| **10 m rig w/ SSB+CW** (Ailunce HS4) | ✅ | ✅ | ❌ no backend | Single band; 10 m is propagation-fickle |
| **All-HF transceiver** (IC-7300, Xiegu G90) | ✅ | ✅ | ✅ Hamlib | The do-everything pick |

<sub>¹ Unless its menu actually has an **SSB/USB/LSB** mode — check before assuming.</sub>

**Rule of thumb:** to *listen* to CW cheaply, an **SDR with HF coverage** (RSP1B, or a
direct-sampling dongle). To *work* CW, a real **transceiver with SSB/CW** — and if you
want the dial and PTT wired into CW Station, pick one with a **Hamlib CAT backend**
(IC-7300, G90, and the like).

### Getting its audio into the decoder

The SDR software does the tuning and demodulation; CW Station decodes the audio it
produces. Install an SDR app and (for the live path) a virtual audio cable:

```bash
brew install --cask gqrx     # SDR receiver app; bundles the RTL-SDR driver
brew install blackhole-2ch   # virtual audio cable (SDR → CW Station), macOS
```

In your SDR app (GQRX shown here): select the RTL-SDR device, tune a CW signal, and set
the mode to **CW-U / CW-L** so it produces a clean audio note. Then pick one of two
paths:

- **Record → Decode.** Record a clip in the SDR app and drop the file on the **Decode**
  page, or run it headless:

  ```bash
  uv run python manage.py cw_decode --wav clip.wav
  ```

- **Live tape.** Set the SDR app's audio output to **BlackHole 2ch**, then point CW
  Station's live capture at that same device and open **CW Monitor → Live**:

  ```bash
  uv run python manage.py cw_monitor_live --list-devices    # find BlackHole's index
  uv run python manage.py cw_monitor_live --device N --stream yourusername
  ```

Either way the same radio-side tips apply: park the signal at one steady pitch with a
narrow filter, prefer slow AGC, and let the tone auto-detect (or pin it with `--tone`).

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
- **The Callbook page** (CW Monitor → Callbook) is the QRZ.com integration home:
  a **lookup console** to review any station's QRZ record alongside your own history
  with them (and log them from there), the **account panel** where you change or
  test the XML password later (write-only — type a new one and Save & test) or
  unlink entirely, and **QRZ logbook sync** — with a per-logbook API key from QRZ
  (My Logbook → Settings → API), *Import* pulls your entire QRZ log in (duplicates
  skipped, safe to repeat) and *Export* pushes contacts logged here up to QRZ,
  marking them so nothing double-sends.
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
