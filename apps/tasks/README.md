# tasks — background-task helpers + reliability

Example `@task` functions (email send, data processing) for the pre-wired `django-tasks-db`
queue, plus a reliability layer on top of that engine:

- **`db_worker`** (shadows django-tasks-db's): same arguments, plus a worker heartbeat and a
  lost-task reaper, so a task whose worker died is marked `FAILED` (`WorkerLost`) instead of
  staying `RUNNING` forever.
- **`@retry_policy`**: opt-in retries with exponential backoff. Each attempt is its own result,
  and attempts are linked by `TaskAttempt`.

**Status:** Framework-provided. `tasks.py` is **example code you can edit or replace** with your
own tasks. The reliability modules are framework code. Recurring jobs live in `apps/scheduler/`
(`@scheduled`).

**Key files:** `tasks.py` (examples), `reliability.py` (retries), `reaper.py` + `heartbeat.py`
(lost tasks), `signals.py`, `models.py` (`WorkerHeartbeat`, `TaskAttempt`),
`management/commands/` (`db_worker`, `reap_tasks`).

**See:** [`../../docs/skills/background-tasks.md`](../../docs/skills/background-tasks.md).
