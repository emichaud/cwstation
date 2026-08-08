# CW Station

A ham-radio CW (Morse) workbench: **decode** CW off the air or from WAV
recordings, **practice** with synthesized PARIS-timed signals, **send** text as
clean keyed audio, and **replay** every pass on a paper-tape monitor — with a
smart **logbook** that matches callsigns, exports ADIF, and syncs to QRZ and
eQSL.

## Start here

- **[Tutorial: Your First Ten Minutes](/help/cw-tutorial/)** — from your first
  decode to working a simulated band, step by step.
- **[CW Station — Operator's Guide](/help/cw-station/)** — the day-to-day
  reference: monitor, decode, send, live tape, and the logbook.
- **[Install & Deploy CW Station](/help/cw-install/)** — dependencies, config,
  and the two production gotchas (ASGI for the live tape, persistent volumes).
- **[Morse Code Reference](/help/morse-reference/)** — every letter, number,
  punctuation mark, and prosign the station speaks.

## What's in the box

| Feature | What it does |
|---------|--------------|
| **Decode** | Recover CW from a WAV file or live audio with the numpy DSP engine |
| **Practice** | Synthesize clean, PARIS-timed CW at any speed to train your ear |
| **Live tape** | Real-time paper-tape monitor of everything the station copies |
| **Send** | Key text as click-free audio, with expanding macros (`{mycall}`, `{call}`, `{rst}`) |
| **Logbook** | Auto-matches callsigns to sessions; smart search; ADIF import/export |
| **QRZ & eQSL** | Callsign lookups and logbook sync, per operator, credentials encrypted at rest |
| **Rig Setup** | Connect a radio via Hamlib with a stoplight walk-through; upload a photo of your own rig |

## A note on your callsign

The send macros fill `{mycall}` from **your login username** — no per-macro
editing. Log in as your callsign (e.g. `N1KRX`) and it just works. See the
[Install guide](/help/cw-install/#3-your-callsign-and-the-send-macros) for the
details and the recommended account setup.
