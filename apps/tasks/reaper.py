"""Find ``RUNNING`` tasks whose worker died, and mark them ``FAILED``.

``django-tasks-db`` never finishes a task whose worker was SIGKILLed or
OOM-killed mid-run — the row stays ``RUNNING`` forever. This module closes that
gap using the :class:`~apps.tasks.models.WorkerHeartbeat` rows SmallStack's
``db_worker`` keeps fresh. A ``RUNNING`` row is **lost** when the worker that
claimed it (``worker_ids[-1]``):

- has a heartbeat row with ``stopped_at`` set (it exited while the task ran), or
- has a heartbeat row older than ``SMALLSTACK_TASKS_LOST_AFTER_SECONDS``, or
- has **no** heartbeat row (a plain upstream worker, or one from before this
  feature) and the task started more than ``SMALLSTACK_TASKS_ORPHAN_SECONDS``
  ago — conservative, since without a heartbeat a long task and a dead worker
  look the same.

A lost row is flipped to ``FAILED`` with a conditional UPDATE
(``status=RUNNING`` in the WHERE), so a worker that finishes the task at the
same moment wins and nothing is overwritten; concurrent reapers (every worker
runs one) are safe for the same reason. The failure records
:class:`~apps.tasks.exceptions.WorkerLost` as its exception class and a one-line reason as the last
traceback line, which is what the scheduler's run summary and the Explorer show.

A reaped task whose ``@retry_policy`` allows it (``retry_lost``, attempts left)
is then retried like any other failure (``reliability.maybe_retry``), and each
pass also runs ``reliability.repair_missed_retries``.

Execution is at-least-once: a worker that is alive but partitioned from the
database can still be finishing a task the reaper already failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone
from django_tasks.base import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from .exceptions import WORKER_LOST_PATH
from .models import TaskAttempt, WorkerHeartbeat
from .reliability import maybe_retry, repair_missed_retries

logger = logging.getLogger(__name__)

# Heartbeat rows for workers that stopped (or vanished) this long ago are
# deleted by the reaper pass. Any task they held was reaped long before.
HEARTBEAT_RETENTION = timedelta(days=7)


def lost_after_seconds() -> int:
    return int(getattr(settings, "SMALLSTACK_TASKS_LOST_AFTER_SECONDS", 90))


def orphan_seconds() -> int:
    return int(getattr(settings, "SMALLSTACK_TASKS_ORPHAN_SECONDS", 86_400))


@dataclass
class LostTask:
    result_id: str
    task_path: str
    worker_id: str
    reason: str


@dataclass
class ReapResult:
    running: int = 0
    reaped: list[LostTask] = field(default_factory=list)
    # Found lost, but a concurrent finish (or another reaper) got there first.
    raced: int = 0
    # Reaped tasks whose retry policy enqueued another attempt.
    retried: int = 0
    # Earlier failures whose retry had been missed (see repair_missed_retries).
    repaired: int = 0
    heartbeats_pruned: int = 0

    def __str__(self) -> str:
        return (
            f"running={self.running} reaped={len(self.reaped)} raced={self.raced} "
            f"retried={self.retried} repaired={self.repaired} heartbeats_pruned={self.heartbeats_pruned}"
        )


def _fmt(ts: datetime) -> str:
    return timezone.localtime(ts).isoformat(timespec="seconds")


def _lost_reason(row: DBTaskResult, worker_id: str, hb: WorkerHeartbeat | None, now: datetime) -> str | None:
    """Why ``row`` is lost, or ``None`` if its worker may still be running it.

    The staleness branch compares against the **database** clock (annotated onto
    ``hb`` as ``db_now`` by :func:`find_lost_tasks`), because that is the clock
    ``heartbeat.beat()`` wrote ``last_seen_at`` with. Using this process's
    ``timezone.now()`` there would make the 90s threshold a measure of NTP skew
    between the worker host and the reaper host. The orphan branch below keeps
    the caller's ``now``: its threshold is a day wide, so skew is irrelevant, and
    there is no heartbeat row to carry a database timestamp anyway.
    """
    if hb is not None:
        where = f"host {hb.hostname or '?'}, pid {hb.pid or '?'}"
        if hb.stopped_at is not None:
            return f"worker {worker_id} ({where}) exited at {_fmt(hb.stopped_at)} while this task was running"
        hb_now = getattr(hb, "db_now", None) or now
        age = (hb_now - hb.last_seen_at).total_seconds()
        if age > lost_after_seconds():
            return (
                f"worker {worker_id} ({where}) stopped heartbeating; last seen {_fmt(hb.last_seen_at)} "
                f"({int(age)}s ago, threshold {lost_after_seconds()}s)"
            )
        return None
    started = row.started_at or row.enqueued_at
    if started < now - timedelta(seconds=orphan_seconds()):
        return (
            f"worker {worker_id or 'unknown'} has no heartbeat record and this task has been "
            f"RUNNING since {_fmt(started)} (threshold {orphan_seconds()}s)"
        )
    return None


def find_lost_tasks(*, now: datetime | None = None) -> tuple[int, list[LostTask]]:
    """Return ``(running_count, lost)`` without changing anything."""
    now = now or timezone.now()
    # RUNNING is bounded by workers x concurrency, so a Python-side join is
    # fine — and portable, unlike filtering on worker_ids[-1] in JSON.
    running = list(
        DBTaskResult.objects.filter(status=TaskResultStatus.RUNNING).only(
            "id", "task_path", "worker_ids", "started_at", "enqueued_at"
        )
    )
    worker_ids = {row.worker_ids[-1] for row in running if row.worker_ids}
    # db_now: the database's clock, read in the same statement as last_seen_at so
    # staleness is skew-free (see _lost_reason and heartbeat.py's docstring).
    heartbeats = {
        hb.worker_id: hb for hb in WorkerHeartbeat.objects.filter(worker_id__in=worker_ids).annotate(db_now=Now())
    }
    lost: list[LostTask] = []
    for row in running:
        worker_id = row.worker_ids[-1] if row.worker_ids else ""
        reason = _lost_reason(row, worker_id, heartbeats.get(worker_id), now)
        if reason:
            lost.append(LostTask(str(row.id), row.task_path, worker_id, reason))
    return len(running), lost


def prune_heartbeats(*, now: datetime | None = None) -> int:
    cutoff = (now or timezone.now()) - HEARTBEAT_RETENTION
    deleted, _ = WorkerHeartbeat.objects.filter(Q(stopped_at__lt=cutoff) | Q(last_seen_at__lt=cutoff)).delete()
    return deleted


def reap_lost_tasks(*, now: datetime | None = None, dry_run: bool = False) -> ReapResult:
    """Mark every lost ``RUNNING`` task ``FAILED`` and retry it if its policy allows.

    Also runs the repair sweep for missed retries and prunes old heartbeat rows.
    """
    now = now or timezone.now()
    running, lost = find_lost_tasks(now=now)
    result = ReapResult(running=running)
    if dry_run:
        result.reaped = lost
        return result
    for task in lost:
        updated = DBTaskResult.objects.filter(id=task.result_id, status=TaskResultStatus.RUNNING).update(
            status=TaskResultStatus.FAILED,
            finished_at=now,
            return_value=None,
            exception_class_path=WORKER_LOST_PATH,
            traceback=f"WorkerLost: {task.reason}",
        )
        if not updated:
            result.raced += 1
            continue
        result.reaped.append(task)
        logger.warning(
            "tasks: reaped lost task %s (%s): %s",
            task.result_id,
            task.task_path,
            task.reason,
            extra={"task_id": task.result_id, "task_path": task.task_path, "worker_id": task.worker_id},
        )
        try:
            if maybe_retry(task.result_id, TaskAttempt.Cause.LOST, now=now):
                result.retried += 1
        except Exception:  # noqa: BLE001 - the repair sweep gets another go
            logger.warning("tasks: retry of reaped task %s failed", task.result_id, exc_info=True)
    result.repaired = repair_missed_retries(now=now)
    result.heartbeats_pruned = prune_heartbeats(now=now)
    return result
