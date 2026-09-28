"""The worker-side half of lost-task detection: register, beat, reap, stop.

SmallStack's ``db_worker`` calls :func:`register_worker` before the upstream
worker loop starts, runs a :class:`HeartbeatThread` beside it, and calls
:func:`mark_stopped` on the way out. The thread refreshes this worker's
:class:`~apps.tasks.models.WorkerHeartbeat` every
``SMALLSTACK_TASKS_HEARTBEAT_SECONDS`` and, every ``REAP_EVERY`` beats, runs
:func:`~apps.tasks.reaper.reap_lost_tasks` — so every worker is also a reaper.
``db_worker`` also calls :func:`safe_reap` once, synchronously, before its
loop starts, so a worker restarted after a crash fails whatever its
predecessor lost before claiming anything new. No scheduler or extra process
involved.

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

from django.conf import settings
from django.db import close_old_connections, connections
from django.db.models.functions import Now

from .models import WorkerHeartbeat
from .reaper import reap_lost_tasks

logger = logging.getLogger(__name__)

# At the default 15s beat, a reap pass roughly once a minute.
REAP_EVERY = 4


def heartbeat_seconds() -> float:
    return float(getattr(settings, "SMALLSTACK_TASKS_HEARTBEAT_SECONDS", 15))


def register_worker(worker_id: str, queues: str) -> None:
    """Create (or revive) this worker's heartbeat row."""
    WorkerHeartbeat.objects.update_or_create(
        worker_id=worker_id,
        defaults={
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


def safe_reap(worker_id: str) -> None:
    """One reaper pass that logs instead of raising."""
    try:
        close_old_connections()
        result = reap_lost_tasks()
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
            close_old_connections()
            beat(self.worker_id)
        except Exception:  # noqa: BLE001 - skip a beat, never kill the worker
            logger.debug("tasks: heartbeat for %s skipped", self.worker_id, exc_info=True)

    def _reap(self) -> None:
        safe_reap(self.worker_id)
