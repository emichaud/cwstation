"""Find ``RUNNING`` tasks whose worker died, and mark them ``FAILED``.

``django-tasks-db`` never finishes a task whose worker was SIGKILLed or
OOM-killed mid-run — the row stays ``RUNNING`` forever. This module closes that
gap using the :class:`~apps.tasks.models.WorkerHeartbeat` rows SmallStack's
``db_worker`` keeps fresh. The join is on ``worker_ids[-1]``, which SmallStack's
``db_worker`` makes a **per-process incarnation** id rather than the operator's
``--worker-id`` label (see ``heartbeat.new_incarnation``) — so a row identifies
the exact process that claimed the task. A ``RUNNING`` row is **lost** when that
process:

- has a heartbeat row with ``stopped_at`` set (it exited while the task ran), or
- has a heartbeat row older than ``SMALLSTACK_TASKS_LOST_AFTER_SECONDS``, or
- has **no** heartbeat row — a plain upstream worker, a claim from before this
  feature, or an incarnation whose row has been pruned — and the task started
  more than ``SMALLSTACK_TASKS_ORPHAN_SECONDS`` ago.

**Only an exact incarnation match may use ``stopped_at`` or the
``LOST_AFTER_SECONDS`` threshold.** Anything less certain falls through to the
day-wide orphan rule: the failure mode to avoid is deciding a *live* task is
lost, because the retry that follows duplicates work. So the reaper fails toward
"can't tell", never toward "alive" (which would strand the task ``RUNNING``
forever) and never toward "dead" on a partial match.

**A worker cannot be dead while it is running the reaper pass.** Every worker is
also a reaper, so a pass must exclude the caller's own incarnation
(``exclude_worker_ids``) — otherwise a worker whose heartbeat writes are failing
reads its own stale row and fails the task it is executing right now. The
``HeartbeatThread`` additionally declines to reap at all once it has missed
several of its own beats: if it cannot write its own liveness, its reading of
everyone else's is not trustworthy either.

A lost row is flipped to ``FAILED`` with a conditional UPDATE
(``status=RUNNING`` in the WHERE), so a worker that finishes the task at the
same moment wins and nothing is overwritten; concurrent reapers (every worker
runs one) are safe for the same reason. The failure records
:class:`~apps.tasks.exceptions.WorkerLost` as its exception class and a one-line reason as the last
traceback line, which is what the scheduler's run summary and the Explorer show.

A reaped task whose ``@retry_policy`` allows it (``retry_lost``, attempts left)
is then retried like any other failure (``reliability.maybe_retry``), and each
pass also runs ``reliability.repair_missed_retries``.

**Execution is at-least-once, and the record can disagree with reality.** Any
worker that is paused or partitioned past ``SMALLSTACK_TASKS_LOST_AFTER_SECONDS``
while running a task reaches this — a starved or throttled container, a
snapshotted or migrated VM, a host that swaps, a long stop-the-world pause, not
just a network partition. The reaper fails the task, a retry runs, and then the
original worker resumes and writes its own result over the reaper's verdict with
an unconditional UPDATE we do not control (upstream's ``set_successful``).

The late completion **wins and is kept**: it is ground truth, the reaper is a
heuristic, and blocking that write would mean patching the engine this layer
exists not to touch. What the reaper guarantees instead is that the disagreement
is never silent. ``reliability._mark_superseded`` stamps ``parent_completed_at``
on the retry's :class:`~apps.tasks.models.TaskAttempt` row, so *the chain
executed more than once* is a durable fact and not just a log line;
``chain_outcome`` reports it (the scheduler puts it in the run message);
``ReapResult.reaped_then_completed`` counts it, separately from the benign
``raced``; and it is logged at WARNING with the task path. A still-``READY``
retry is cancelled best-effort — by then it has usually already started, and
cancelling it would not un-execute it.

So a task can run twice. When the reaper is the reason, it cannot run twice
*unnoticed*.

That scope is deliberate and it is the whole claim. A task can still repeat work
for reasons this module never sees — the classic one being a call that succeeded
remotely and lost its reply, so the attempt records a failure and a retry redoes
work the server already did (``tasks.py`` concedes this for SMTP). No reap, no
supersession, nothing here to notice. Making *those* safe is the task author's
job, via idempotency; making the reaper's own duplicates visible is this
module's.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Now
from django.tasks import TaskResultStatus
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from .exceptions import WORKER_LOST_PATH
from .models import TaskAttempt, WorkerHeartbeat
from .reliability import detect_late_completions, maybe_retry, repair_missed_retries

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
    # Found lost, but a concurrent finish (or another reaper) got there first, so
    # the conditional UPDATE did not apply. Benign: nothing was reaped, nothing
    # was retried, the task ran exactly once.
    raced: int = 0
    # Reaped *and* retried, and then the original completed anyway — the chain
    # executed more than once. Deliberately not folded into `raced`: that one
    # means the reaper lost a race harmlessly, this one means it was overruled
    # by ground truth after acting on a wrong verdict.
    reaped_then_completed: int = 0
    # Reaped tasks whose retry policy enqueued another attempt.
    retried: int = 0
    # Earlier failures whose retry had been missed (see repair_missed_retries).
    repaired: int = 0
    heartbeats_pruned: int = 0

    def __str__(self) -> str:
        return (
            f"running={self.running} reaped={len(self.reaped)} raced={self.raced} "
            f"retried={self.retried} repaired={self.repaired} "
            f"reaped_then_completed={self.reaped_then_completed} "
            f"heartbeats_pruned={self.heartbeats_pruned}"
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
        # The operator's --worker-id reads better in a remediation string than the
        # incarnation composite; the pid distinguishes incarnations anyway.
        name = hb.label or worker_id
        where = f"host {hb.hostname or '?'}, pid {hb.pid or '?'}"
        if hb.stopped_at is not None:
            return f"worker {name} ({where}) exited at {_fmt(hb.stopped_at)} while this task was running"
        hb_now = getattr(hb, "db_now", None) or now
        age = (hb_now - hb.last_seen_at).total_seconds()
        if age > lost_after_seconds():
            return (
                f"worker {name} ({where}) stopped heartbeating; last seen {_fmt(hb.last_seen_at)} "
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


def find_lost_tasks(
    *, now: datetime | None = None, exclude_worker_ids: Collection[str] = ()
) -> tuple[int, list[LostTask]]:
    """Return ``(running_count, lost)`` without changing anything.

    ``exclude_worker_ids`` holds incarnations that are known-alive because they
    are *executing this call* — see :func:`reap_lost_tasks`.
    """
    now = now or timezone.now()
    exclude = frozenset(exclude_worker_ids)
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
        if worker_id and worker_id in exclude:
            continue
        reason = _lost_reason(row, worker_id, heartbeats.get(worker_id), now)
        if reason:
            lost.append(LostTask(str(row.id), row.task_path, worker_id, reason))
    return len(running), lost


def prune_heartbeats(*, now: datetime | None = None) -> int:
    cutoff = (now or timezone.now()) - HEARTBEAT_RETENTION
    deleted, _ = WorkerHeartbeat.objects.filter(Q(stopped_at__lt=cutoff) | Q(last_seen_at__lt=cutoff)).delete()
    return deleted


def reap_lost_tasks(
    *, now: datetime | None = None, dry_run: bool = False, exclude_worker_ids: Collection[str] = ()
) -> ReapResult:
    """Mark every lost ``RUNNING`` task ``FAILED`` and retry it if its policy allows.

    Also runs the repair sweep for missed retries and prunes old heartbeat rows.

    ``exclude_worker_ids`` is the caller's own incarnation(s), and encodes the
    invariant **a worker cannot be dead while it is running the reaper pass**.
    Without it a worker whose heartbeat writes were failing — a starved container,
    a saturated database, SQLite write contention — would read its *own* stale
    ``last_seen_at``, conclude the task it is at that moment executing is lost,
    fail it as ``WorkerLost`` and enqueue attempt 2 alongside the still-running
    attempt 1. Observed in testing: the reaper decided exactly this and only
    SQLite's database-wide write lock stopped the UPDATE from landing; on
    PostgreSQL, or on SQLite once the lock frees, it lands.

    Pass the **incarnation**, never the label: two live processes sharing a
    ``--worker-id`` must still be able to reap each other's dead incarnations,
    which is the whole point of keying liveness on the process.
    """
    now = now or timezone.now()
    running, lost = find_lost_tasks(now=now, exclude_worker_ids=exclude_worker_ids)
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
    # Catches a reaped-then-completed attempt whose worker died before it could
    # send task_finished, so the signal receiver never saw it.
    result.reaped_then_completed = len(detect_late_completions(now=now))
    result.heartbeats_pruned = prune_heartbeats(now=now)
    return result
