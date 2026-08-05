# CW Station — Operator's Guide

The CW Station decodes and sends Morse through your sound card — no CAT control, no
special hardware. Three pages do the work: **Monitor** (the paper tape), **Decode**,
and **Send**. Every pass is saved as a session you can replay.

## Decoding off the air

1. Record the signal as a WAV — from your receiver's audio out into your computer,
   or export from SDR software (GQRX, SDR++, etc.). Mono or stereo, 8/16/32-bit PCM.
2. Open **Decode → Off the air**, pick the file, and set **Tone** to the pitch of the
   signal — the sidetone you actually hear, commonly 500–800 Hz. This is the one knob
   that matters: the decoder listens in a narrow window around that frequency.
3. Decode. You get the copy with per-character confidence (shaky characters show in
   red), the sender's speed, and any callsigns spotted in the copy. Open the session
   to replay it on the tape.

**Tips from the bench:**

- The decoder is fully adaptive on speed — you never set WPM for receive. It buffers
  the opening marks, works out the dit length, then replays them, so the first
  character isn't lost.
- If the copy is empty or garbage, the tone is almost always wrong. Tune your
  receiver so the CW note sits near your usual sidetone pitch and try that number.
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

Sessions are searchable (the topbar search finds text in your copy) and private to
your account. Audio for synthesized sessions is regenerated on demand; uploaded
recordings are not stored — only their decode.

## Command line

The same engine runs headless:

```bash
# sanity check, no radio needed
uv run python manage.py cw_decode --text "CQ CQ DE N0CALL K" --wpm 22

# decode a recording
uv run python manage.py cw_decode --wav signal.wav --tone 700

# watch the QSO-draft bridge extract callsigns/RST
uv run python manage.py cw_decode --text "CQ TEST DE W1AW 599" --log
```

## What's next

The engine is built as a seam: fldigi/WSJT-X taps, an ML decoder for weak signals,
and a proper QSO logbook all plug into the same event stream without replumbing.
See `specs/mvp-cw-decode-design.md` in the repo for the roadmap.
