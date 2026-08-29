# FM Radio

**CW Monitor → FM Radio**

Broadcast FM through the SDR dongle, on a tuner faceplate: six presets, a
seven-segment readout, a dial, and seek. Audio plays out of the machine running
CW Station.

It exists because FM is what a plain RTL stick does best — and because a strong,
always-on signal is the quickest way to confirm the receiver chain works at all.

---

## Using it

**Tune** — drag the needle, nudge with **−** / **+**, or press a preset. While
you're on air, moving the dial retunes live.

**◀◀ / ▶▶ Seek** — scans to the next strong station and skips the empty space
between. It sweeps the whole band once (~3 s, digits blinking), then locks the
next carrier that rises at least 12 dB above the noise floor, wrapping around at
the band edges. If nothing qualifies, whatever was playing resumes and it says so.

**★ Set** — saves the current frequency as a preset (P1–P6). Click a preset to
tune it; the **✕** on a preset removes it.

**■ Stop** — silence, immediately.

---

## The model plate

The bottom edge of the faceplate is the receiver's status line:

```
Nooelec, NESDR SMArt v5, SN: 86661822        ● ANT  ● RTL  ● AUD   rescan
```

Three lamps, three independent things that can be missing:

| Lamp | Green means | Red means |
|---|---|---|
| **ANT** | A dongle is detected | Nothing plugged in — or it isn't an RTL device |
| **RTL** | The `rtl-sdr` tools are installed | `brew install librtlsdr` |
| **AUD** | Audio output is available | `uv sync --extra dev --extra live` |

When any is red, the exact fix appears below the chassis. Press **rescan** after
plugging or swapping a dongle — the device list is cached.

---

## Why FM only

The band is 88–108 MHz and there is no AM option, because **AM broadcast
(530–1710 kHz) is below the tuner floor** on RTL sticks — the same limitation
that puts 40 m and 20 m CW out of reach. It isn't weak there; there's no signal
path at all. See [SDR Hardware](sdr-hardware).

AM as a *mode* would work for airband, which is in range; it just isn't part of
this page.

---

## Notes

- **The dongle does one thing at a time.** Starting an
  [Antenna Survey](antenna-survey) while the radio is playing is refused rather
  than silently cutting the audio.
- **Audio comes out of the machine running the server**, not the browser. On a
  local install those are the same machine; on a deployed instance there's no
  dongle at all and the page shows its empty state.
- Presets are **per operator**.

---

## Troubleshooting

**Stop doesn't stop it**
Fixed — but if you ever see it again, something outside the app is holding the
device:

```bash
pkill -x rtl_fm
```

**Static instead of a station**
FM needs a real signal; the dial doesn't know what's out there. Use **seek**,
which measures before it tunes, or check what's actually receivable with an
[instant check](antenna-survey).

**Seek finds nothing**
Nothing in the band cleared 12 dB over the noise floor. Usually the antenna —
try an [Antenna Survey](antenna-survey); if FM broadcast is weak there, seek has
nothing to work with.
