# Antenna Survey

**CW Monitor → Antennas**

Answers one question honestly: *does this antenna hear better than that one?*
Connect an antenna, sweep a set of bands, swap, sweep again — the comparison
table puts the numbers side by side.

---

## The two buttons

**⚡ Instant check** — sweeps the always-on bands (~7 s) and shows the result.
No name, nothing saved. This is the one to use while you're physically holding an
antenna, or to see whether a band is worth listening to right now.

**Save as a run** — sweeps the bands you ticked, under an antenna name, and keeps
it. Saved runs are what the comparison table is built from.

---

## Reading the numbers

Each band reports **SNR in dB** — the strongest signal minus the band's median
noise floor. Higher is better.

| Reading | Means |
|---|---|
| under 3 dB | nothing heard |
| 3–10 dB | faint |
| 10–20 dB | workable |
| over 20 dB | strong |

Two things the number is *not*:

- **Not absolute signal strength.** A bigger antenna raises the noise floor as
  well as the signal; what improves is the gap between them. That's why SNR is
  reported and not raw power.
- **Not comparable across dongles or gains.** Both are recorded with every run,
  and the table flags a mismatch rather than letting you draw a false conclusion.

---

## Why "always on" bands matter most

Bands tagged **always on** carry transmitters that never stop:

- **NOAA weather** (162.4–162.56 MHz) — 24/7, high power. The single best antenna
  test signal in the list.
- **FM broadcast** (88–108 MHz) — always on and local; the easiest signal there is.
- **10 m beacons** (28.2–28.3 MHz) — the IBP beacon network, around the clock.
- **WWV 10 MHz** — the time station, 24/7. The best HF check, if your hardware
  reaches HF at all.

A change in one of these **is** a change in your antenna.

By contrast, a quiet ham band tells you about **propagation**, not hardware. The
10 m CW segment reads near zero on a superb antenna when the band is closed. Don't
judge an antenna by it.

> **Rule of thumb:** if NOAA doesn't move, the antenna didn't get better.

---

## Running a fair comparison

1. **Keep the gain identical** between runs. The slider snaps to the steps your
   tuner actually has, and the value is stored per run; mismatched columns are
   flagged.
2. **Keep the same receiver.** Two dongles measure two different front ends.
3. **Sweep the same bands** each time — the table only compares what overlaps.
4. **Ignore differences under about 3 dB.** Run-to-run scatter is real; only a
   consistent, several-dB gap is a genuine winner.
5. **Change one thing at a time.** Moving the antenna *and* swapping it tells you
   nothing about which mattered.

---

## The bands

| Band | Range | Notes |
|---|---|---|
| 80 m ham | 3.5–3.6 MHz | HF — needs direct sampling or an upconverter |
| 40 m CW | 7.0–7.04 MHz | The busiest CW segment. HF |
| WWV 10 MHz | 9.995–10.005 MHz | Always on. HF. The best HF antenna check |
| 20 m CW | 14.0–14.07 MHz | HF |
| 12 m ham | 24.89–24.99 MHz | Right at the tuner floor — often nothing |
| CB / 11 m | 26.965–27.405 MHz | Busy by day on an HF-capable antenna |
| 10 m CW | 28.0–28.07 MHz | Reachable on a plain stick. Propagation-dependent |
| 10 m beacons | 28.2–28.3 MHz | Always on |
| 10 m SSB | 28.3–28.5 MHz | |
| 6 m ham | 50.0–50.3 MHz | Often dead |
| FM broadcast | 88–108 MHz | Always on |
| Airband | 118–137 MHz | Bursty — a low reading may just mean no traffic |
| 2 m ham | 144–148 MHz | |
| NOAA weather | 162.4–162.56 MHz | Always on |

Bands tagged **HF** sit below the ~24 MHz tuner floor. They're swept if you ask,
but a plain RTL stick will read flat — see [SDR Hardware](sdr-hardware).

---

## Worked example

The bench this feature was built on, three antennas, same dongle, same gain:

| Band | Telescope | Curly whip | Stock whip |
|---|---|---|---|
| FM broadcast | 24.1 | 26.5 | 26.0 |
| 2 m ham | 8.3 | 7.8 | 9.6 |
| NOAA weather | 0.7 | 1.0 | 3.6 |
| 10 m beacons | 1.0 | 0.6 | 1.6 |

Read: **all three are effectively the same antenna.** Every difference is within
a few dB — noise, not a result. And NOAA at 0.7–3.6 dB, where a working antenna
should be 20+, says all three are poor. The fix isn't another whip; it's a
different *kind* of antenna — a wire for HF, a proper ground plane or dipole for
VHF.

That is the useful outcome: it stopped three swaps from being mistaken for
progress.

---

## Notes

- Surveys are **per operator** — yours are yours.
- A sweep takes about **2 seconds per band**; the estimate updates as you tick.
- The dongle does one thing at a time: a survey is refused while the
  [FM Radio](fm-radio) is playing. Press Stop there first.
- Press **rescan** after swapping dongles — the device list is cached.
