---
title: Scheduler
description: Recurring background jobs — declare a cadence in code, manage it from a themed control console
---

# Scheduler

> **Building this?** Read the agent-facing skill first: [`docs/skills/scheduler.md`](https://github.com/emichaud/django-smallstack/blob/main/docs/skills/scheduler.md). It's prescriptive (what to do); this page is the reference (why + worked examples).

Some work has to happen on a clock, not on a click: a nightly rollup, an hourly fetch, a digest email. The scheduler runs [background tasks](/smallstack/help/smallstack/background-tasks/) on a **repeating cadence** — cron expressions, intervals, or once-at-a-time — with no Celery, Redis, or worker fleet to operate. It builds directly on the same DB-backed task engine everything else uses: the scheduler owns *timing, overlap, and history*; the task engine owns *execution and results*.

The dashboard lives at `/smallstack/scheduler/` — stat cards, a 24-hour run timeline, upcoming and recent runs (each with its return-value summary), and a per-job **Run now**.

## Declaring a schedule

Schedules are **code-owned**: you create them by decorating a task, and manage them (pause, retune) from the UI. There is deliberately no create-a-schedule-from-scratch form — a schedule with no code behind it can't run anything.

```python
# apps/sportstats/tasks.py
from django.tasks import task
from apps.scheduler import scheduled

@scheduled(cron="15 * * * *", name="Hourly boxscore fetch")   # :15 past each hour
@task
def fetch_boxscores():
    raw = fetch_provider()
    load_into_db(raw)
    return {"loaded": len(raw)}
```

Pick exactly one cadence:

| Argument | Meaning |
|---|---|
| `every="5m"` | interval — units `s/m/h/d/w`, or calendar-aware `mo`/`y` |
| `cron="0 6 * * *"` | 5-field cron, evaluated in the job's timezone (DST-correct) |
| `at=<datetime>` | run once at a specific time |

Useful extras: `anchor="12-25"` phase-locks an interval ("every year from Dec 25"), `timezone=`, `catch_up="run_once"|"skip"` (what happens after downtime), and `allow_overlap=False` (skip a fire while the previous run is still going — the default).

On startup each app's `tasks.py`/`schedules.py` is autodiscovered and every `@scheduled` spec syncs into a job row. Cadence refreshes from code on each deploy; the **enabled** flag stays under your control, so pausing a job survives a redeploy.

## Managing from the console

Each job's control page (`/smallstack/scheduler/jobs/<id>/`) lets an operator pause it, retune the cadence (which re-seeds the next run immediately), or fire **Run now**. A cadence edited in the UI sticks as an override — the next deploy won't stomp it. The same management surface exists over REST (`GET` + `PATCH`) and MCP (`list_schedules`, `update_schedule`) for scripts and AI agents.

## How firing works

A per-minute **tick** selects enabled jobs whose next run is due, atomically claims each (two concurrent ticks can never double-fire), enqueues the task, and records a run. Use exactly one trigger per deployment:

| Trigger | When |
|---|---|
| `POST /smallstack/scheduler/tick/` (localhost-only) | **Default** — the shipped `scripts/smallstack-cron` line |
| `manage.py run_due_tasks` | system cron / systemd timer |
| `manage.py scheduler_beat` | foreground 60s loop for local dev |

A run is recorded as **queued** when enqueued; a later reconcile pass (each tick, and every dashboard load) reads the task engine's result and promotes it to **success**/**failed** with the return value or exception on the run row. So a just-fired job showing "queued" for a minute is normal — open the dashboard and it flips.

## Retries

Set **Retries on failure** on a job's control page (or `max_retries` over REST/MCP, up to 10) to run it again when it fails, waiting a little longer each time. A run that's retrying stays **queued**. The overlap guard treats it as still running, and it ends **success** ("(attempt 2)") if a retry works or **failed** ("after 3 attempts: …") if none does. The failure email is sent once, after the last attempt. `0` (the default) leaves it to the task: a task with its own `@retry_policy` still retries.

**What it does and does not retry.** It retries a run whose task **raised** an error. It does *not* re-run a task whose **worker was killed mid-run** (a deploy, an OOM kill, a crashed container): that task may already have done half its work, and re-running it would repeat those effects. Only a developer can declare a task safe to repeat that way, with `@retry_policy(retry_lost=True)` in the code. If a run fails with `WorkerLost`, this setting leaves it failed on purpose.

This setting also sets the *number* of attempts only. If the task's code already declares which errors are worth retrying — e.g. "SMTP errors only" — that still applies, so raising the number won't make it retry an error the task's author excluded. See [Background Tasks](/smallstack/help/smallstack/background-tasks/) for how retries work.

## Trying it locally

```bash
uv run python manage.py scheduler_beat --once   # tick: enqueue due jobs
uv run python manage.py db_worker               # drain the queue
uv run python manage.py scheduler_beat --once   # tick again: reconcile outcomes
```

Then watch `/smallstack/scheduler/`. The scheduler also registers a monitor on `/smallstack/status/` that trips if an enabled job is overdue — a proxy for "the tick isn't firing."

## Framework jobs that ride on it

The framework dogfoods its own primitive: approvals' expiry sweep (`Approvals: expire overdue requests`, every 5 minutes) and notifications' retention prune (`Notifications: prune old rows`, daily) are ordinary `@scheduled` jobs you'll see in the console.

## Related

- [Background Tasks](/smallstack/help/smallstack/background-tasks/) — the task engine underneath (one-shot enqueue, the worker, queues)
- [Approvals](/smallstack/help/smallstack/approvals/) / [Notifications](/smallstack/help/smallstack/notifications/) — shipped jobs that use the scheduler
