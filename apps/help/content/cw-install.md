---
title: "Install & Deploy CW Station"
description: "From a bare machine to a running station — dependencies, config, and the two production gotchas"
icon: "server"
---

# Install & Deploy CW Station

This guide takes you from a bare machine to a running CW Station — first for
local use, then for a real deployment. CW Station runs on a single machine or
container with **no external services**: SQLite, a background worker, and (when
you have a radio) Hamlib for CAT control.

Two things about CW Station differ from a plain web app, and both bite in
production if missed. They're called out as **⚠ Gotcha** below:

1. The **live tape uses WebSockets** → you must run an **ASGI** server, not the
   default WSGI/gunicorn command.
2. Credentials, logbook, and rig photos are **stateful** → their files must live
   on **persistent volumes**, or you lose data (and can't decrypt QRZ/eQSL
   passwords) on redeploy.

---

## 1. Dependencies

| What | Why | Install |
|------|-----|---------|
| **Python 3.12+** | runtime | your OS / pyenv |
| **uv** | package manager (the project uses it throughout) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Hamlib** | CAT rig control (`rigctld`) — only if you connect a radio | macOS `brew install hamlib` · Debian/Ubuntu `sudo apt install hamlib-utils` |

Everything else (numpy DSP engine, Channels/daphne, Pillow, django-tasks-db) is
a Python dependency installed by `uv sync`. **No Redis, no Postgres required.**

Hamlib is **optional**: without it you can still decode WAV files, run the
practice simulator, and use the paper-tape monitor. The Rig Setup page detects
a missing Hamlib and tells the operator how to install it.

---

## 2. Local install

```bash
git clone <your-repo> cw-station && cd cw-station
make setup      # uv sync + migrate + create admin/admin + seed sample rig photos
make run        # dev server on http://localhost:8010
```

`make setup` is idempotent — re-run it anytime. It creates a dev superuser
(`admin` / `admin`) and seeds a few **sample rig photos** so the Rig Setup page
has something to show. Log in and visit `/cw/` for the monitor, `/cw/rig/setup/`
to connect a radio.

To decode from the command line with no browser:

```bash
uv run python manage.py cw_decode --text "CQ CQ DE N0CALL K" --wpm 22
uv run python manage.py cw_decode --wav signal.wav --tone 700
```

---

## 3. Your callsign and the send macros

The send macros (`CQ`, `73`, RST reply, …) contain placeholders like
`{mycall}`, `{call}`, and `{rst}`. **You don't edit each macro** — placeholders
expand when you insert a macro:

- **`{mycall}`** fills automatically from **your login username** (upper-cased).
  So if you log in as `N1KRX`, every macro keys `N1KRX` with no editing. The
  ADIF export's `STATION_CALLSIGN` uses the same source.
- **`{call}`** fills from the **current contact** — the station you're working,
  picked up from the decoded copy / QSO context.
- **`{rst}`** defaults to `599`.
- **Any other placeholder** you invent (e.g. `{qth}`) stays put and the cursor
  lands on it so you can type the value once, in place.

> **Deploy tip:** because `{mycall}` is the login username, the cleanest setup
> is to **make each operator's username their callsign**. Create accounts at
> `/smallstack/manage/users/` (or the Django admin) named for the call. If you'd
> rather decouple them (a dedicated "Station Callsign" profile field), that's a
> small addition — ask and it can be wired in.

---

## 4. Production configuration

Set these via environment variables (or a `.env` file). The settings module for
production is `config.settings.production`.

```bash
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=<50+ random chars>          # required; never reuse the dev key
ALLOWED_HOSTS=cw.example.com
DEBUG=False

# CW Station specifics
CW_CREDENTIALS_KEY_FILE=/data/.cw_credentials_key   # see gotcha #2
MEDIA_ROOT=/data/media                              # rig photos live here
SMALLSTACK_DOCS_ENABLED=False                        # framework reference docs hidden

# Optional integrations (per-operator creds are set in the UI, not here)
# QRZ XML + logbook and eQSL are configured per operator at /cw/callbook/
```

CW Station carries **no manufacturer rig photos** (copyright) — it ships only
line-art illustrations. Each operator uploads a photo of their own radio on the
Rig Setup page; those uploads land in `MEDIA_ROOT`.

---

## 5. ⚠ Gotcha #1 — run an ASGI server (the live tape needs it)

The live paper-tape at `/cw/live/` streams over a **WebSocket** (Django
Channels). A WSGI server can't carry that connection. The stock `Dockerfile`
ships a **WSGI** command:

```dockerfile
CMD ["gunicorn", "-c", "/app/gunicorn.conf", "config.wsgi:application"]   # ← WSGI: no WebSocket
```

For CW Station, serve the **ASGI** app (`config.asgi:application`) with daphne
(bundled) or uvicorn. Override the command:

```dockerfile
# Dockerfile — replace the CMD
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
```

or in `docker-compose.yml` / Kamal, set the container command to the same. If
you keep gunicorn for the HTTP surface, run daphne/uvicorn alongside it and
route `/ws/` (and `/cw/live/` upgrades) to the ASGI process. The simplest
correct deployment is **daphne serving everything**.

Quick check after deploy: open `/cw/live/`, start the simulator
(`make sim` or the Simulator page) and confirm characters stream onto the tape
in real time. If the page loads but never updates, you're on WSGI.

---

## 6. ⚠ Gotcha #2 — persist the stateful files

Three paths hold data that **must survive a redeploy**. In Docker, mount a
volume (e.g. `/data`) and point each at it:

| Path | Holds | Lose it and… |
|------|-------|--------------|
| `db.sqlite3` | sessions, logbook (QSOs), rig config | your whole log is gone |
| `.cw_credentials_key` (`CW_CREDENTIALS_KEY_FILE`) | the Fernet key that encrypts QRZ/eQSL passwords at rest | stored passwords become **undecryptable** — operators must re-enter them |
| `MEDIA_ROOT` | operator-uploaded rig photos | photos revert to illustrations |

```yaml
# docker-compose.yml — persist all three under one volume
services:
  web:
    command: daphne -b 0.0.0.0 -p 8000 config.asgi:application
    environment:
      - CW_CREDENTIALS_KEY_FILE=/data/.cw_credentials_key
      - MEDIA_ROOT=/data/media
      - DATABASE_URL=sqlite:////data/db.sqlite3
    volumes:
      - cwdata:/data
volumes:
  cwdata:
```

The credentials key is generated on first use if absent (0600, gitignored).
Generating a **new** key can't read data encrypted with the old one — so back
it up alongside the database.

---

## 7. Background worker & CAT daemon

- **Background worker** — `django-tasks-db`. The Docker entrypoint runs
  `db_worker` inline by default (single-container). For higher throughput run it
  as its own process/container: `python manage.py db_worker --queue-name "*"`.
- **rigctld (CAT)** — CW Station starts and supervises `rigctld` itself from the
  Rig Setup page; you don't run it by hand. It only needs the `rigctld` binary
  present (Hamlib installed) **on the machine wired to the radio**. In a
  container, that means installing Hamlib in the image *and* passing the serial
  device through (`--device /dev/ttyUSB0`) — so radio-attached deployments are
  typically **bare-metal or a privileged container on the shack computer**,
  while a cloud instance runs decode/practice/logbook only.

---

## 8. Deploy targets

The repo includes three ready paths — all work, pick one:

- **Docker** — `Dockerfile` + `docker-entrypoint.sh` (runs migrate +
  collectstatic + inline worker). Apply the ASGI CMD from §5.
- **docker-compose** — `docker-compose.yml` for a single-host stack. Add the
  volumes + command from §6.
- **Kamal** — `config/deploy.yml` (edit `service`, `image`, `servers`, and the
  registry). Set the app command to daphne and declare the `/data` volume.

Whichever you choose, the deploy checklist is the same:

1. `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG=False` set
2. **ASGI** server command (§5)
3. `db.sqlite3`, `.cw_credentials_key`, `MEDIA_ROOT` on a **persistent volume** (§6)
4. Hamlib in the image only if the host has a radio (§7)
5. `make backup` scheduled (SQLite snapshot with retention)
6. Smoke test: log in → `/cw/live/` streams → `/cw/log/` loads → `/cw/rig/setup/`
   detects Hamlib (or explains its absence)

---

## Related

- **Operator's Guide** — day-to-day use of the monitor, decode, send, and log
- **Tutorial** — first ten minutes, from decode to a simulated QSO
- **Morse Reference** — every character and prosign the station speaks
