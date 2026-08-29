# SDR Hardware & Compatibility

Which receivers CW Station can drive, what each one can and can't hear, and how
to tell the difference between "my antenna is bad", "this band is closed", and
"my dongle physically cannot go there".

If you're about to buy something, read [Choosing hardware](#choosing-hardware).

---

## What CW Station talks to

The app drives **RTL-SDR dongles** directly, through the `rtl-sdr` command-line
tools (`rtl_test`, `rtl_fm`, `rtl_power`). That covers the very common
RTL2832U-based sticks — Nooelec NESDR, RTL-SDR Blog, generic "DVB-T" dongles.

| Receiver | Detected? | Notes |
|---|---|---|
| **RTL-SDR sticks** (RTL2832U) | ✅ Yes | NESDR, RTL-SDR Blog V3/V4, generic DVB-T. Plug in and press **rescan**. |
| **SDRplay** (RSP1B, RSPdx) | ❌ No | Different driver (SoapySDR / vendor API). Not detected. |
| **Airspy** (R2, Mini, HF+) | ❌ No | Same reason. |
| **HackRF** | ❌ No | Same reason. |
| **A transceiver's audio out** | ✅ Yes, differently | Not "an SDR" to this app — it arrives as *sound*, via **CW Monitor → Live**. See [Using a real radio instead](#using-a-real-radio-instead). |

If you plug in an SDRplay or Airspy, the pages will say **"No SDR detected"**.
That isn't a bug — the app genuinely can't see it. Those receivers work
beautifully with CW Station, just through the audio route rather than the direct
one: run their own software (SDRuno, SDR#, gqrx) and feed its audio into the
live monitor.

---

## The tuner floor — why HF may be missing

Most RTL sticks use an **R820T/R828D** tuner, which has no signal path below
roughly **24 MHz**. Everything below that — 80 m, 40 m, 30 m, 20 m, the AM
broadcast band, shortwave — is not weak, it is *absent*. You get a flat noise
floor and nothing else.

This matters because **the busiest CW is on 40 m and 20 m**, both below the
floor.

What's still reachable on a plain stick:

- **10 m (28.0–28.07 MHz CW)** — above the floor, genuinely usable. Propagation-
  dependent: it's excellent when the band is open and silent when it isn't.
- **6 m, 2 m** ham bands
- **FM broadcast, airband, NOAA weather** and everything else VHF/UHF

### Two ways past the floor

**1. Direct sampling** — some sticks tap the ADC directly, bypassing the tuner.
This is a *hardware* feature: the **RTL-SDR Blog V3/V4** have it; the NESDR
SMArt and most generic dongles do not. No setting substitutes.

CW Station supports it. On the **Antenna Survey** page, tick **Direct sampling**;
the HF bands (80/40/20 m, WWV) then become meaningful. The Receiver line tells
you whether your stick is known to have the tap:

> Rafael Micro R820T tuner · 29 gain steps · **no known ADC tap**: HF needs an
> upconverter, or a stick like the RTL-SDR Blog V3

Turning it on for a dongle without the tap produces noise, not signal.

**2. An upconverter** — a small box (Ham It Up, SpyVerter, ~$50) that shifts HF
up into the range the tuner *can* reach. Works with any stick, including one
that also does direct sampling, and generally performs better. You tune the
shifted frequency (HF + the converter's crystal, usually 125 MHz).

---

## Choosing hardware

Roughly in order of cost:

| If you want… | Get |
|---|---|
| VHF/UHF only (FM, airband, NOAA, 2 m) | Any RTL stick — what you likely already have |
| HF cheaply, accepting compromises | **RTL-SDR Blog V3** (~$40) — direct sampling built in, 8-bit and unfiltered on HF |
| HF properly, still using this app's direct control | RTL-SDR Blog V3 **+ an upconverter** (~$90 total) |
| The best receiver, accepting the audio route | **SDRplay RSP1B** (~$130) — 14-bit, real HF front end. Not auto-detected; feed its audio to the live monitor |

**The antenna usually matters more than the receiver.** A $130 SDR on a 20 cm
whip will lose to a $25 stick on a wire in a tree. Measure before you buy — see
[Antenna Survey](antenna-survey).

---

## Running more than one dongle

If two RTL sticks are plugged in, both pages show a **Receiver** picker and every
sweep and tune is addressed to the chosen one.

Two things to know:

- **The dongle is exclusive.** Only one thing can use it at a time. Starting a
  survey while the FM Radio is playing is refused with a message rather than
  silently cutting your audio; press Stop first.
- **Surveys record which receiver took them.** Comparing an antenna measured on
  one stick against an antenna measured on another compares *receivers*, not
  antennas — the comparison table flags it when the columns don't match.

---

## Gain

The tuner has a fixed list of gain steps (29 on the R820T, from 0.0 to 49.6 dB).
CW Station reads that list from your actual device and snaps to it, so the gain
recorded with a survey is the gain that was used.

For comparing antennas, the exact value matters far less than **keeping it the
same across runs**. Different gains make two runs incomparable, and the
comparison table says so.

---

## Using a real radio instead

A transceiver or a non-RTL SDR reaches CW Station as **audio**, which is the
better route for serious CW anyway:

1. Run the radio's own software (gqrx, SDRuno, SDR#) or connect the rig's audio
   out / data port.
2. Set the mode to **CW-U/CW-L** with a narrow filter (500 Hz or tighter — this
   is the single biggest improvement you can make).
3. Route the audio to an input the machine can capture. On macOS, install a
   virtual cable: `brew install blackhole-2ch`.
4. Point the live monitor at it:

```bash
make devices                       # find the input's index
DEVICE=0 make monitor              # decode onto the Live tape
```

Full detail in the [Operator's Guide](cw-station).

---

## Troubleshooting

**"No SDR detected" but something is plugged in**
Press **rescan** — the device list is cached, and it doesn't refresh itself when
you swap sticks. If it's still missing, it's probably not an RTL device (see the
table above). Check the machine sees it at all:

```bash
rtl_test -t
```

**Every band reads ~0 dB, including FM broadcast**
FM broadcast is the easiest signal there is. If that's flat, suspect the antenna
connection or the antenna itself before anything else.

**HF bands read flat with direct sampling on**
Either the stick has no ADC tap (check the Receiver line), or the band is closed.
Sweep **WWV 10 MHz** — it transmits 24 hours a day, so if WWV is silent it's the
hardware, not propagation.

**The receiver is busy / "already in use"**
Something else has the dongle: gqrx, another browser tab, or a stray process.

```bash
pkill -x rtl_fm      # release a stuck receiver
```

**Audio plays but nothing decodes**
The decoder needs a steady tone in a narrow passband. Check the mode is CW-U/CW-L
(not FM), narrow the filter, and let the tone auto-detect. Band noise is handled
by the squelch gate — see the [Operator's Guide](cw-station).
