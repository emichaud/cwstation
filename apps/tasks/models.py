"""Worker liveness for the django-tasks-db queue.

``django-tasks-db`` records which worker claimed a task (``worker_ids[-1]``)
but not whether that worker is still alive, so a task whose worker is
SIGKILLed / OOM-killed / replaced mid-run stays ``RUNNING`` forever.
SmallStack's ``db_worker`` (``management/commands/db_worker.py``) writes one
:class:`WorkerHeartbeat` row per worker process and refreshes it on a timer;
``reaper.reap_lost_tasks()`` uses those rows to tell a long task from a dead one.
"""

from __future__ import annotations

from django.db import models


class WorkerHeartbeat(models.Model):
    """One row per ``db_worker`` **process incarnation**.

    ``worker_id`` is not the operator's ``--worker-id``: it is
    ``"<label>#<token>"``, where the token is minted fresh at process start
    (``heartbeat.new_incarnation``). That composite is what ``db_worker`` hands to
    upstream's worker, so it is exactly what lands in ``DBTaskResult.worker_ids``
    — which makes the task → claimant join *exact per process*, with no upstream
    patch. ``label`` keeps the operator's name for logs, Explorer and the
    "which worker died" strings, and is deliberately **not** unique.

    Why an incarnation and not a pool member: every consumer asks a question
    about a process ("is the thing that claimed this task still alive?"). While
    the row was keyed on the operator's label, a worker restarting under a stable
    ``--worker-id`` revived its dead predecessor's row, so the task that
    predecessor left ``RUNNING`` looked live and was never reaped — and two live
    processes sharing a label made one's clean exit reap the other's in-flight
    task. Neither is rescuable by tuning a threshold; the join was ambiguous by
    construction.
    """

    # Width is deliberately generous, not fitted. The arithmetic today is a
    # 64-char label ceiling (upstream's validate_worker_id) + "#" + a 12-char
    # token = 77, which fitted 80 with three characters spare — a margin that
    # only holds until someone widens the token or upstream relaxes its limit.
    # Overflow is engine-dependent in the dangerous direction: Postgres raises,
    # but MySQL in non-strict mode truncates silently, and a *truncated worker
    # identity collides* — two processes sharing one row, which is exactly the
    # W1/W2 duplicate-execution class this column exists to prevent. A failed
    # start is loud; a silent collision is not, so don't bet on the margin.
    worker_id = models.CharField(max_length=128, unique=True, help_text="label#token — matches worker_ids[-1].")
    label = models.CharField(max_length=64, blank=True, db_index=True, help_text="The operator's --worker-id.")
    hostname = models.CharField(max_length=255, blank=True)
    pid = models.PositiveIntegerField(null=True, blank=True)
    queues = models.CharField(max_length=255, blank=True, help_text="The worker's --queue-name value.")
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(db_index=True)
    # Set on clean exit (and on a forced second-signal exit). A RUNNING task
    # whose worker has stopped_at is lost immediately, no staleness wait.
    stopped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        verbose_name = "Worker heartbeat"
        verbose_name_plural = "Worker heartbeats"

    def __str__(self) -> str:
        state = "stopped" if self.stopped_at else "running"
        return f"{self.label or self.worker_id} ({self.hostname or '?'}:{self.pid or '?'}, {state})"


class TaskAttempt(models.Model):
    """Links one ``DBTaskResult`` to the attempt chain it belongs to.

    Every retry is a fresh ``DBTaskResult`` (so each attempt keeps its own
    traceback); this row records which result it retried (``parent_id``) and
    the chain's first result (``root_id``). The first attempt has no row unless
    something needs to pin a per-chain override (``max_attempts``) — a result
    id with no row is attempt 1 of a chain rooted at itself.

    ``parent_id`` is unique: whichever of the worker's signal receiver, the
    reaper, or the repair sweep retries a failed attempt first wins, and any
    other path's insert rolls back with its enqueue — never two retries.
    """

    class Cause(models.TextChoices):
        FIRST = "first", "First attempt"
        FAILED = "failed", "Task raised"
        LOST = "lost", "Worker lost"
        INTERRUPTED = "interrupted", "Worker force-stopped"
        MANUAL = "manual", "Manual retry"

    result_id = models.UUIDField(unique=True)
    root_id = models.UUIDField(db_index=True)
    parent_id = models.UUIDField(unique=True, null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    cause = models.CharField(max_length=16, choices=Cause.choices, default=Cause.FIRST)
    # Per-chain override of the task's retry policy (e.g. a scheduled job's
    # max_retries + 1). Copied onto each retry so the chain keeps it.
    max_attempts = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Set when the attempt this row replaced (``parent_id``) reached a terminal
    # state *anyway*, after the reaper had declared it lost and enqueued this
    # retry. Its presence means the chain **executed more than once**: the reaper
    # is a heuristic and the late completion is ground truth, so the record has
    # to survive rather than be quietly overwritten. A log line is not an audit
    # trail — this is the durable half. ``parent_status`` keeps the outcome so
    # the marker is self-contained and reporting needs no second query.
    parent_completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    parent_status = models.CharField(max_length=16, blank=True)
    # True when this retry was still READY at that moment and was cancelled, so
    # it never executed. Then the chain ran *once* (the parent's late completion)
    # and the reaper's mistake cost nothing — which is a different fact from a
    # genuine double execution, and ``chain_outcome`` must not confuse them: a
    # cancelled retry is not the chain's outcome, the parent that finished is.
    retry_cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["root_id", "attempt"]
        verbose_name = "Task attempt"
        verbose_name_plural = "Task attempts"

    def __str__(self) -> str:
        return f"{self.result_id} (attempt {self.attempt} of chain {self.root_id}, {self.cause})"
