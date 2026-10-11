"""The worker-side half of lost-task detection: register, beat, reap, stop.

SmallStack's ``db_worker`` calls :func:`register_worker` before the upstream
worker loop starts, runs a :class:`HeartbeatThread` beside it, and calls
:func:`mark_stopped` on the way out. The thread refreshes this worker's
:class:`~apps.tasks.models.WorkerHeartbeat` every
``SMALLSTACK_TASKS_HEARTBEAT_SECONDS`` and, every ``REAP_EVERY`` beats, runs
:func:`~apps.tasks.reaper.reap_lost_tasks` — so every worker is also a reaper.
``db_worker`` also calls :func:`safe_reap` once, synchronously, before its loop
starts. That pass can only fail a predecessor that is *already* reapable (it
exited cleanly, or its heartbeat is already stale past the threshold); a
supervisor that restarts a crashed worker within the staleness window leaves
tasks too fresh to judge, and the thread's own pass picks them up once the
threshold elapses. No scheduler or extra process involved either way.

The thread measures *process* liveness, not task progress: it keeps beating
while the main thread runs a long task. A hung task on a live worker is a
timeout problem, not a lost-worker one.

Nothing here may take the worker down. Every beat and reap swallows its own
exceptions (e.g. SQLite ``database is locked`` while the main thread holds the
write lock) and simply tries again next beat.

**One clock governs.** Every timestamp here is written with SQL ``Now()``, not
``timezone.now()``, so ``last_seen_at`` is on the *database* clock. Staleness is
a comparison between a value one host wrote and a threshold another host
evaluates, so with app-server wall clocks it is only as accurate as the fleet's
NTP skew: a worker whose clock ran 2 minutes slow looked permanently dead at the
90s threshold, and its live tasks got reaped and (by default) retried. The
reaper reads its side of the comparison from the same clock — see
``reaper.find_lost_tasks``.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid

from django.conf import settings
from django.db import connections
from django.db.models.functions import Now

from .models import WorkerHeartbeat
from .reaper import lost_after_seconds, reap_lost_tasks

logger = logging.getLogger(__name__)

# At the default 15s beat, a reap pass roughly once a minute.
REAP_EVERY = 4

# After this many consecutive failed beats, a worker stops reaping until one
# succeeds. Two, not one: a single skip is ordinary write contention, but by the
# second this process is a third of the way to looking dead itself, and a reaper
# that can't prove its own liveness shouldn't be ruling on anyone else's.
MAX_MISSED_BEATS_BEFORE_YIELDING = 2


def heartbeat_seconds() -> float:
    return float(getattr(settings, "SMALLSTACK_TASKS_HEARTBEAT_SECONDS", 15))


# Separates the operator's --worker-id from the per-process token in the value
# handed to upstream's worker. Cosmetic only: `label` is stored in its own
# column, so nothing ever needs to parse this back apart.
INCARNATION_SEP = "#"


def new_incarnation(label: str) -> str:
    """A worker id unique to *this process*, prefixed with the operator's label.

    This is the value ``db_worker`` gives upstream, so it is what upstream's
    ``DBTaskResult.claim()`` appends to ``worker_ids`` — which is what makes the
    reaper's task → claimant join exact per process rather than per label. The
    token is random, not a counter: two processes must never agree on one, and
    there is no coordination point that could hand out sequence numbers.
    """
    return f"{label}{INCARNATION_SEP}{uuid.uuid4().hex[:12]}"


def register_worker(worker_id: str, label: str, queues: str) -> None:
    """Record this process incarnation as alive.

    ``worker_id`` is the composite from :func:`new_incarnation`, so this always
    inserts a new row — it can never revive, and therefore never conceal, a dead
    predecessor's row the way a label-keyed row did.
    """
    WorkerHeartbeat.objects.update_or_create(
        worker_id=worker_id,
        defaults={
            "label": label[:64],
            "hostname": socket.gethostname()[:255],
            "pid": os.getpid(),
            "queues": queues[:255],
            "last_seen_at": Now(),
            "stopped_at": None,
        },
    )


def beat(worker_id: str) -> None:
    WorkerHeartbeat.objects.filter(worker_id=worker_id).update(last_seen_at=Now())


def mark_stopped(worker_id: str) -> None:
    """Record a clean exit. Best-effort: a failure here must not mask the exit."""
    try:
        WorkerHeartbeat.objects.filter(worker_id=worker_id).update(last_seen_at=Now(), stopped_at=Now())
    except Exception:  # noqa: BLE001 - shutting down; staleness catches it anyway
        logger.warning("tasks: could not mark worker %s stopped", worker_id, exc_info=True)


def refresh_owned_connections() -> None:
    """``close_old_connections()``, minus any connection the caller is using.

    Django's ``close_if_unusable_or_obsolete()`` closes a connection whose
    autocommit differs from its settings, and *every* connection inside an
    ``atomic()`` block qualifies. So calling ``close_old_connections()`` from
    inside a caller's transaction closes the connection out from under it. On
    Postgres that kills the transaction ("the connection is closed"). SQLite's
    in-memory test database ignores the close, which hid this. The worker's own
    calls run in autocommit and are unaffected. This guard is for everyone else
    who calls :func:`safe_reap` (tests, a shell, a caller inside ``atomic()``):
    a connection in an atomic block belongs to that caller and is left alone.
    """
    for conn in connections.all(initialized_only=True):
        if not conn.in_atomic_block:
            conn.close_if_unusable_or_obsolete()


def safe_reap(worker_id: str) -> None:
    """One reaper pass that logs instead of raising.

    Excludes ``worker_id`` — this process is demonstrably alive, since it is the
    one running the pass. See ``reaper.reap_lost_tasks``: without this a worker
    whose beats were failing would fail the task it is itself executing.
    """
    try:
        refresh_owned_connections()
        result = reap_lost_tasks(exclude_worker_ids={worker_id})
        if result.reaped:
            logger.info("tasks: reaper (%s): %s", worker_id, result)
    except Exception:  # noqa: BLE001 - try again next pass
        logger.warning("tasks: reaper pass on %s failed", worker_id, exc_info=True)


class HeartbeatThread(threading.Thread):
    """Daemon thread: beat every ``interval`` seconds, reap every ``reap_every`` beats."""

    def __init__(self, worker_id: str, *, interval: float | None = None, reap_every: int = REAP_EVERY) -> None:
        super().__init__(name=f"heartbeat-{worker_id}", daemon=True)
        self.worker_id = worker_id
        self.interval = heartbeat_seconds() if interval is None else interval
        self.reap_every = max(1, reap_every)
        self._stop_event = threading.Event()
        # Consecutive failed beats. Reset by a successful beat; while it is at or
        # above MAX_MISSED_BEATS_BEFORE_YIELDING this worker declines to reap.
        self.missed_beats = 0

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)

    def run(self) -> None:
        beats = 0
        try:
            while not self._stop_event.wait(self.interval):
                self._beat()
                beats += 1
                if beats % self.reap_every == 0:
                    self._reap()
        finally:
            # This thread owns its connection(s); don't leak them.
            connections.close_all()

    def _beat(self) -> None:
        try:
            refresh_owned_connections()
            beat(self.worker_id)
        except Exception:  # noqa: BLE001 - skip a beat, never kill the worker
            self.missed_beats += 1
            # WARNING, not DEBUG: a failed beat is the first move in every way
            # this subsystem goes wrong — it is how a live worker starts looking
            # dead to other reapers, and it used to be invisible. Report how
            # close we are to the threshold so the line is actionable.
            logger.warning(
                "tasks: heartbeat for %s skipped (%d consecutive; tasks are declared lost after %ss)",
                self.worker_id,
                self.missed_beats,
                lost_after_seconds(),
                exc_info=True,
            )
        else:
            if self.missed_beats:
                logger.info(
                    "tasks: heartbeat for %s recovered after %d missed beat(s)",
                    self.worker_id,
                    self.missed_beats,
                )
            self.missed_beats = 0

    def _reap(self) -> None:
        # Can't write our own liveness => can't be trusted to judge anyone else's,
        # and we may already look dead to them. Reaping from here is how a starved
        # worker kills live tasks (its own excluded, but not its siblings').
        if self.missed_beats >= MAX_MISSED_BEATS_BEFORE_YIELDING:
            logger.warning(
                "tasks: worker %s skipping its reaper pass — %d consecutive heartbeat failures",
                self.worker_id,
                self.missed_beats,
            )
            return
        safe_reap(self.worker_id)
