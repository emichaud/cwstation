# Band Chart

**CW Monitor → Band Chart**

Where you may transmit, and where the CW operators actually are. The live chart
with proportional bars is at [Band Chart](/cw/bands/); this page is the
explanation behind it.

> **US amateur allocations, IARU Region 2.** Regions 1 and 3 differ — most
> sharply on 40 m, which stops at 7.200 MHz elsewhere. Before you transmit,
> check the [ARRL band chart](https://www.arrl.org/band-plan) and the current
> FCC rules. They are the authority; this is a bench reference.

---

## Two kinds of line

Every band chart mixes two things that behave very differently, and confusing
them is the most common beginner mistake:

| | Set by | Binding? |
|---|---|---|
| **Subband edges** | FCC Part 97.301 / 97.305 | **Yes — law.** Transmitting outside your class's segment is a violation. |
| **Calling frequencies** | IARU and club band plans | **No — convention.** Nobody is breaking a rule by working 7.031 instead of 7.030. |

The bars on the chart page show the first kind. The watering-hole tables show
the second.

---

## HF at a glance

| Band | Range | Technician gets | Notes |
|---|---|---|---|
| 160 m | 1.800–2.000 MHz | nothing | No FCC subbands; CW low, phone above ~1.843 by convention |
| 80/75 m | 3.500–4.000 MHz | 3.525–3.600 CW | Regional at night |
| 60 m | 5 channels | nothing | Channelised, 100 W ERP, 2.8 kHz max |
| 40 m | 7.000–7.300 MHz | 7.025–7.125 CW | The busiest CW band on earth |
| 30 m | 10.100–10.150 MHz | nothing | **CW and data only** — no phone, ever. 200 W |
| 20 m | 14.000–14.350 MHz | nothing | The DX workhorse |
| 17 m | 18.068–18.168 MHz | nothing | WARC — no contests |
| 15 m | 21.000–21.450 MHz | 21.025–21.200 CW | Daytime DX, follows the solar cycle |
| 12 m | 24.890–24.990 MHz | nothing | WARC. Right at a bare dongle's tuner floor |
| 10 m | 28.000–29.700 MHz | 28.000–28.300 CW · 28.300–28.500 phone | The only HF phone a Technician gets |

Unless noted, the limit is **1500 W PEP**. Exceptions: 30 m is 200 W, 60 m is
100 W PEP ERP, and Novice/Technician HF operation is capped at 200 W.

---

## What each license class gets

**Technician** — full privileges on everything above 50 MHz, plus four slices of
HF: CW on 80, 40 and 15, and CW/data *and phone* on 10 m. That 10 m phone
segment (28.300–28.500) is the one place a Technician can have an HF voice
contact.

**General** — all bands, but the phone segments start higher than an Extra's.
This is the licence that opens HF properly.

**Advanced** — closed to new issues since 1997, still held by many. Sits between
General and Extra on phone.

**Extra** — the full allocation, including the bottom 25 kHz of 80/40/20/15 m,
where the DX and contest CW piles up.

The pattern worth remembering: **CW segments live at the bottom of every band,
phone above.** Move up in licence class and you gain width at the bottom of the
CW segment and the bottom of the phone segment.

---

## Where the CW operators are

Gathering points, not channels — tune a few kHz either side.

| Band | QRP | SKCC | FISTS |
|---|---|---|---|
| 160 m | 1.810 | 1.820 | — |
| 80 m | 3.560 | 3.550 | 3.558 |
| 40 m | 7.030 | 7.055 | 7.058 |
| 30 m | 10.106 | 10.120 | 10.118 |
| 20 m | 14.060 | 14.050 | 14.058 |
| 17 m | 18.096 | 18.080 | 18.085 |
| 15 m | 21.060 | 21.050 | 21.058 |
| 12 m | 24.906 | 24.910 | 24.918 |
| 10 m | 28.060 | 28.050 | 28.058 |

- **QRP** — 5 W output or less.
- **SKCC** — Straight Key Century Club; mechanical keys only, no keyers.
- **FISTS** — the CW operators' club. "Accuracy transcends speed."

On VHF, CW and weak-signal work sit at the very bottom of each band: **50.090**
on 6 m, **144.050** on 2 m, **432.100** on 70 cm.

---

## Which band, and when

Propagation picks the band more than you do.

| If you want | Try | When |
|---|---|---|
| A first contact, reliably | 40 m | Anytime; regional by day, long-haul at night |
| DX across an ocean | 20 m | Daylight and the hours either side |
| A quiet band during a contest | 30 m or 17 m | WARC bands are contest-free by agreement |
| Something to hear at solar maximum | 15 m, 10 m | Daytime |
| Local ragchewing at night | 80 m | After dark |

The Antenna Survey's 10 m beacon band (28.2–28.3) is the fastest propagation
check you have: the IBP beacons transmit around the clock, so if you hear them,
10 m is open. See [Antenna Survey](antenna-survey).

---

## What this receiver can hear

CW Station's SDR support is a **receiver**, and a plain RTL-SDR stick can't
reach most of this chart:

- **Below ~24 MHz** — 160 through 15 m — needs direct sampling (an RTL-SDR Blog
  V3/V4) or an upconverter. A bare NESDR SMArt reads flat there no matter how
  good the antenna is.
- **12 m and 10 m** sit at or above the tuner floor, so a plain stick can try.
- **6 m and up** is fully in reach, which is why the
  [Antenna Survey](antenna-survey) leans on VHF reference bands.

The full hardware story is in [SDR Hardware](sdr-hardware).

---

## Notes

- Nothing on this page is per-operator — it's a fixed reference, the same for
  everyone.
- The data behind both this document and the chart page lives in one module
  (`apps/cw/bandplan.py`), so the two can't drift apart.
- Band edges do change. The FCC moved the 60 m channels and re-farmed 3.4 GHz
  within recent memory; re-check anything you're about to key up on.
