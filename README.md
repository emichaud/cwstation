# CW Station

*A ham-radio CW (Morse) workbench — decode off the air, practice, send, and log.*

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![Django 6.1](https://img.shields.io/badge/django-6.1-green)
![License MIT](https://img.shields.io/badge/license-MIT-brightgreen)
![Morse · CW](https://img.shields.io/badge/mode-CW%20%C2%B7%20Morse-e0b84c)

CW Station copies Morse code from a recording or straight off your receiver, lets you
**practice** against clean synthesized signals, **sends** text as click-free keyed audio,
and replays every pass on a **paper-tape monitor** — with a smart **logbook** that matches
callsigns, exports ADIF, and syncs to QRZ and eQSL.

It runs entirely on your own machine: **SQLite**, no external services, no radio required
to get started. Attach a rig later and it talks CAT over Hamlib.

---

## What it does

- **Decode** — recover CW from a WAV/MP3/FLAC/OGG recording or live sound-card audio. A
  Django-free numpy DSP engine finds the CW note automatically and rides the sender's
  speed on its own. (W1AW code-practice files work as-is.)
- **Practice** — synthesize clean, PARIS-timed CW at any speed, with adjustable noise, to
  train your ear and test the decoder.
- **Live tape** — a real-time paper-tape monitor of everything the station copies, streamed
  over a WebSocket. No radio? A **band simulator** feeds the same tape; an **fldigi tap**
  brings in PSK31 and other digital modes.
- **Send** — key text into properly timed, click-free CW audio. Message macros expand
  `{mycall}`, `{call}`, and your own `{tags}`; an on-air toggle keys the rig via PTT.
- **Logbook** — contacts are matched to the session they were heard in. Smart search, ADIF
  import/export, and QRZ + eQSL sync — credentials encrypted at rest.
- **Rig control** — connect a radio through Hamlib with a stoplight walk-through; the live
  panel shows the dial, and you can upload a photo of your own rig.
- **Tutor mode** — expands CW shorthand and prosigns into plain English on any tape, so you
  learn the abbreviations in context.

The operator UI is a clean, industrial console with five color palettes and light/dark
themes. A staff-only admin console (dashboard, activity, backups, REST/MCP surfaces) sits
one click away.

## Quick start

Full first-run walkthrough for a fresh Mac (Homebrew → Python → uv → clone → run) is in
**[SETUP-MACOS.md](SETUP-MACOS.md)**. The short version, once you have
[uv](https://docs.astral.sh/uv/) and Python 3.12+:

```bash
git clone https://github.com/emichaud/cwstation.git
cd cwstation
make setup     # deps + migrate + a dev admin (admin / admin) + sample data
make run       # dev server on http://localhost:8010/
```

Prefer your own account? Skip the demo admin:

```bash
uv sync --all-extras && uv run python manage.py migrate
uv run python manage.py createsuperuser   # use your callsign as the username → fills {mycall}
make run
```

Decode from the command line, no browser needed:

```bash
uv run python manage.py cw_decode --text "CQ CQ DE N0CALL K" --wpm 22
uv run python manage.py cw_decode --wav signal.wav --tone 700
```

## How it works & where the docs are

- **In-app help** at `/smallstack/help/` — the Operator's Guide, a ten-minute tutorial, a
  Morse reference, and **Install & Deploy**.
- **How CW Station Works** (`/cw/architecture/`) — a signal-flow blueprint of how audio
  comes in, gets decoded, and goes back out, and where the rig, simulator, and libraries fit.
- Deployment (Docker / Kamal), including the two gotchas (serve ASGI for the live tape;
  persist the DB + credentials key), is in the Install & Deploy guide.

## Handy commands

```bash
make run        # dev server (port 8010)
make test       # full pytest suite
make lint       # ruff + template-comment check
make migrate    # apply migrations
make backup     # SQLite snapshot with retention
uv run python manage.py cw_decode --text "599 TU 73" --wpm 26   # headless decode
```

## Tech, briefly

Django + a Django-free **numpy** DSP core (decode + synth), **Django Channels/daphne** for
the live tape, **Hamlib** (`rigctld`) for CAT rig control, optional **sounddevice** for
sound-card capture, an optional **fldigi** XML-RPC tap, and Fernet-encrypted credentials.
SQLite by default; runs on a single machine or container.

## License

MIT — see [LICENSE](LICENSE).

---

<sub>Built on **Django SmallStack**, a small-footprint Django foundation (themed admin,
REST/MCP/search surfaces, CLI). Framework docs: **[www.smallstack.site](https://www.smallstack.site/)**
· source: **[github.com/emichaud/django-smallstack](https://github.com/emichaud/django-smallstack)**.</sub>
