"""Retries with backoff for ``django.tasks`` on the django-tasks-db backend.

``django-tasks-db`` has no attempts: a task that raises ends ``FAILED``. This
module adds retries on top without touching the engine. Declare a policy
above ``@task`` (the same convention as ``@scheduled``)::

    from django.tasks import task
    from apps.tasks.reliability import retry_policy

    @retry_policy(max_attempts=3, backoff=5, backoff_max=600, retry_on=(OSError,))
    @task(queue_name="email")
    def send_email_task(...): ...

When an attempt fails, :func:`maybe_retry` enqueues a **fresh copy** of the
task (same args, queue, priority, backend) with ``run_after = now + delay``,
and records a :class:`~apps.tasks.models.TaskAttempt` linking it to the chain.
Every attempt is its own ``DBTaskResult`` row, so every traceback is kept.

Three paths call :func:`maybe_retry`:

- ``signals.py`` — the worker's ``task_finished`` signal, right after a
  failure (the normal path);
- ``reaper.py`` — right after it fails a task whose worker died
  (``cause=lost``) — governed by the policy's ``retry_lost``;
- :func:`repair_missed_retries` — a sweep (run with every reaper pass) for
  failures whose retry never got enqueued, e.g. the worker died between
  recording the failure and sending the signal.

The unique ``TaskAttempt.parent_id`` makes those paths race-safe: the enqueue
and the attempt row commit together, so a second retry of the same attempt
rolls back instead of duplicating.

**Retries are opt-in** (``SMALLSTACK_TASKS_DEFAULT_MAX_ATTEMPTS = 1``):
execution is at-least-once, so only tasks that are safe to repeat should
retry. A task with no policy fails exactly as it did before.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string
from django_tasks.base import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from .exceptions import INTERRUPTED_PATHS, WORKER_LOST_PATH
from .models import TaskAttempt

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How far back the repair sweep looks for failures with no retry, and how long
# it leaves a fresh failure alone (the signal receiver is about to handle it).
REPAIR_WINDOW = timedelta(hours=1)
REPAIR_GRACE = timedelta(seconds=30)
REPAIR_BATCH = 100


@dataclass(frozen=True)
class RetryPolicy:
    """How a task retries. ``None`` fields fall back to the settings defaults."""

    max_attempts: int | None = None
    backoff: float | None = None
    backoff_max: float | None = None
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    # Retry when the worker died (reaped as WorkerLost) or was force-stopped
    # mid-task (SystemExit/KeyboardInterrupt), not only when the task raised.
    retry_lost: bool = True

    def attempts(self) -> int:
        if self.max_attempts is not None:
            return self.max_attempts
        return int(getattr(settings, "SMALLSTACK_TASKS_DEFAULT_MAX_ATTEMPTS", 1))

    def delay(self, attempt: int) -> float:
        """Seconds to wait before attempt ``attempt + 1``: doubling, capped, jittered.

        Equal jitter (half fixed, half random) spreads a burst of failures out
        without ever retrying immediately.
        """
        initial = self.backoff if self.backoff is not None else _setting("SMALLSTACK_TASKS_BACKOFF_SECONDS", 5)
        cap = (
            self.backoff_max if self.backoff_max is not None else _setting("SMALLSTACK_TASKS_BACKOFF_MAX_SECONDS", 600)
        )
        base = min(cap, initial * 2 ** max(0, attempt - 1))
        return base / 2 + random.uniform(0, base / 2)  # noqa: S311 - jitter, not crypto


DEFAULT_POLICY = RetryPolicy()

# The policy a chain gets when an *operator* pinned a budget (pin_max_attempts —
# e.g. a ScheduledJob with max_retries > 0) onto a task that declares no
# @retry_policy of its own. Identical to DEFAULT_POLICY except that it will not
# re-run an attempt whose worker died.
#
# Re-running a task the worker was killed *inside* is an at-least-once decision
# about a task body nobody marked repeatable: it may have already written half
# its side effects. A developer writing @retry_policy(...) is asserting the task
# is safe to repeat, so retry_lost=True is theirs to choose. An operator typing a
# number into the scheduler form is asking "try the failure again" and cannot
# know whether the body is idempotent — so the operator control must never be
# more aggressive than the explicit decorator. A task that *raised* reported its
# own failure, which is exactly what the operator asked to retry.
IMPLICIT_PINNED_POLICY = RetryPolicy(retry_lost=False)

# task path (Task.module_path, == DBTaskResult.task_path) -> policy.
_POLICIES: dict[str, RetryPolicy] = {}


def _setting(name: str, default: float) -> float:
    return float(getattr(settings, name, default))


def retry_policy(
    *,
    max_attempts: int | None = None,
    backoff: float | None = None,
    backoff_max: float | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    retry_lost: bool = True,
) -> Callable[[T], T]:
    """Declare how a ``@task`` retries. Apply it *above* ``@task``.

    ``max_attempts`` counts the first run (3 = one run + up to two retries).
    ``backoff`` is the first delay in seconds; it doubles per attempt up to
    ``backoff_max``. ``retry_on`` limits retries to those exception classes
    (and their subclasses). Returns the task unchanged.
    """
    if max_attempts is not None and max_attempts < 1:
        raise ValueError("max_attempts counts the first run and must be >= 1")
    policy = RetryPolicy(
        max_attempts=max_attempts,
        backoff=backoff,
        backoff_max=backoff_max,
        retry_on=tuple(retry_on),
        retry_lost=retry_lost,
    )

    def decorator(task_obj: T) -> T:
        path = getattr(task_obj, "module_path", None)
        if not isinstance(path, str):
            raise TypeError("@retry_policy must be applied above @task (it decorates a Task, not a function)")
        _POLICIES[path] = policy
        return task_obj

    return decorator


def policy_for(task_path: str) -> RetryPolicy:
    """The policy for ``task_path``, importing it first so its decorator has run.

    The reaper and repair sweep can see a task whose module this process never
    imported; importing it registers any ``@retry_policy`` it declares.
    """
    if task_path not in _POLICIES:
        try:
            import_string(task_path)
        except Exception:  # noqa: BLE001 - an unimportable task gets the defaults
            logger.debug("tasks: could not import %s to read its retry policy", task_path, exc_info=True)
    return _POLICIES.get(task_path, DEFAULT_POLICY)


@dataclass(frozen=True)
class AttemptInfo:
    root_id: UUID
    attempt: int
    max_attempts: int | None  # per-chain override, if any


def attempt_info(result_id: UUID | str) -> AttemptInfo:
    """Where ``result_id`` sits in its chain (attempt 1 of itself if unrecorded)."""
    row = TaskAttempt.objects.filter(result_id=result_id).first()
    if row is None:
        return AttemptInfo(root_id=UUID(str(result_id)), attempt=1, max_attempts=None)
    return AttemptInfo(root_id=row.root_id, attempt=row.attempt, max_attempts=row.max_attempts)


def pin_max_attempts(result_id: UUID | str, max_attempts: int) -> None:
    """Override the retry budget for the chain starting at ``result_id``.

    For callers that know better than the task's decorator for one enqueue
    (the scheduler's per-job ``max_retries``). Call right after enqueueing, in
    the same transaction, so no worker can claim and fail the task first.

    This pins the **attempt count only**. Which exceptions retry (``retry_on``),
    whether a worker death retries (``retry_lost``) and the backoff still come
    from the task's own ``@retry_policy`` — and when the task has none, from
    :data:`IMPLICIT_PINNED_POLICY`, which is deliberately more conservative than
    ``DEFAULT_POLICY``.
    """
    TaskAttempt.objects.update_or_create(
        result_id=result_id,
        defaults={"root_id": result_id, "attempt": 1, "max_attempts": max_attempts},
    )


def _classify(row: DBTaskResult, cause: str) -> str:
    if row.exception_class_path == WORKER_LOST_PATH:
        return TaskAttempt.Cause.LOST
    if row.exception_class_path in INTERRUPTED_PATHS:
        return TaskAttempt.Cause.INTERRUPTED
    return cause


def _matches(exception_class_path: str, retry_on: tuple[type[BaseException], ...]) -> bool:
    try:
        exc_class = import_string(exception_class_path)
    except Exception:  # noqa: BLE001 - can't resolve it; only a catch-all matches
        return any(cls in (Exception, BaseException) for cls in retry_on)
    return isinstance(exc_class, type) and issubclass(exc_class, retry_on)


@dataclass(frozen=True)
class _Decision:
    cause: str
    policy: RetryPolicy
    info: AttemptInfo
    budget: int


def _decide(row: DBTaskResult, cause: str) -> _Decision | None:
    """The retry decision for a ``FAILED`` row, or ``None`` if it won't be retried."""
    if row.status != TaskResultStatus.FAILED:
        return None
    cause = _classify(row, cause)
    policy = policy_for(row.task_path)
    info = attempt_info(row.id)
    if info.max_attempts is not None and policy is DEFAULT_POLICY:
        # Operator-pinned budget, no @retry_policy on the task: don't re-run a
        # worker death nobody declared repeatable. See IMPLICIT_PINNED_POLICY.
        policy = IMPLICIT_PINNED_POLICY
    budget = info.max_attempts if info.max_attempts is not None else policy.attempts()
    if info.attempt >= budget:
        return None
    if cause in (TaskAttempt.Cause.LOST, TaskAttempt.Cause.INTERRUPTED):
        if not policy.retry_lost:
            return None
    elif not _matches(row.exception_class_path, policy.retry_on):
        return None
    return _Decision(cause=cause, policy=policy, info=info, budget=budget)


def maybe_retry(
    result_id: UUID | str, cause: str = TaskAttempt.Cause.FAILED, *, now: datetime | None = None
) -> str | None:
    """Enqueue the next attempt of a failed task if its policy allows.

    Returns the new result id, or ``None`` when no retry was enqueued (not
    failed, budget spent, exception not retryable, or another path already
    retried this attempt).
    """
    row = DBTaskResult.objects.filter(id=result_id).first()
    decision = _decide(row, cause) if row is not None else None
    if row is None or decision is None:
        return None
    cause, policy, info, budget = decision.cause, decision.policy, decision.info, decision.budget

    now = now or timezone.now()
    delay = policy.delay(info.attempt)
    try:
        task_obj: Any = import_string(row.task_path)
        with transaction.atomic():
            new = task_obj.using(
                queue_name=row.queue_name,
                priority=row.priority,
                backend=row.backend_name,
                run_after=now + timedelta(seconds=delay),
            ).enqueue(*row.args_kwargs.get("args", []), **row.args_kwargs.get("kwargs", {}))
            TaskAttempt.objects.create(
                result_id=new.id,
                root_id=info.root_id,
                parent_id=row.id,
                attempt=info.attempt + 1,
                cause=cause,
                max_attempts=info.max_attempts,
            )
    except IntegrityError:
        # Another path retried this attempt first (unique parent_id).
        return None
    logger.info(
        "tasks: retrying %s (%s) as %s: attempt %d of %d in %.0fs (%s)",
        row.id,
        row.task_path,
        new.id,
        info.attempt + 1,
        budget,
        delay,
        cause,
        extra={"task_id": str(row.id), "retry_id": str(new.id), "task_path": row.task_path},
    )
    return str(new.id)


@dataclass(frozen=True)
class ChainOutcome:
    """Where a chain of attempts stands, read from its first result id.

    ``status`` is the latest attempt's engine status (``READY`` / ``RUNNING`` /
    ``SUCCESSFUL`` / ``FAILED``), or ``RETRYING`` while the latest attempt
    failed but another is on its way: the signal receiver is about to enqueue
    it, or the repair sweep will. ``row`` is the latest attempt's result.
    """

    status: str
    row: DBTaskResult
    attempt: int


RETRYING = "RETRYING"

# Chains are bounded by max_attempts; this only guards a corrupt parent loop.
_MAX_CHAIN = 1000


def chain_outcome(result_id: UUID | str, *, now: datetime | None = None) -> ChainOutcome | None:
    """Follow ``result_id``'s retries to the latest attempt and report its state.

    ``None`` if the result doesn't exist (e.g. pruned). A failed latest attempt
    counts as ``RETRYING`` only while it's still inside the repair sweep's
    window: past that, nothing will retry it, so it is final.
    """
    current = str(result_id)
    attempt = 1
    for _ in range(_MAX_CHAIN):
        child = TaskAttempt.objects.filter(parent_id=current).values_list("result_id", "attempt").first()
        if child is None:
            break
        current, attempt = str(child[0]), child[1]
    row = DBTaskResult.objects.filter(id=current).first()
    if row is None:
        return None
    status = str(row.status)
    if status == TaskResultStatus.FAILED:
        now = now or timezone.now()
        recent = row.finished_at is None or row.finished_at >= now - REPAIR_WINDOW
        if recent and _decide(row, TaskAttempt.Cause.FAILED) is not None:
            status = RETRYING
    return ChainOutcome(status=status, row=row, attempt=attempt)


def repair_missed_retries(*, now: datetime | None = None) -> int:
    """Retry recent failures whose retry never got enqueued. Returns retries made.

    Looks at ``FAILED`` rows finished between ``REPAIR_WINDOW`` and
    ``REPAIR_GRACE`` ago that no attempt row names as its parent, narrowed to
    tasks that can retry at all (a policy allowing more than one attempt, or a
    chain with a pinned budget). Capped at ``REPAIR_BATCH`` per pass.
    """
    now = now or timezone.now()
    recent = DBTaskResult.objects.filter(
        status=TaskResultStatus.FAILED,
        finished_at__gte=now - REPAIR_WINDOW,
        finished_at__lte=now - REPAIR_GRACE,
    ).exclude(id__in=TaskAttempt.objects.filter(parent_id__isnull=False).values("parent_id"))
    retryable_paths = [
        path for path in recent.values_list("task_path", flat=True).distinct() if policy_for(path).attempts() > 1
    ]
    pinned = TaskAttempt.objects.filter(max_attempts__gt=1).values("result_id")
    candidates = (
        recent.filter(Q(task_path__in=retryable_paths) | Q(id__in=pinned))
        .order_by("finished_at")
        .values_list("id", flat=True)[:REPAIR_BATCH]
    )
    retried = 0
    for result_id in list(candidates):
        try:
            if maybe_retry(result_id, now=now):
                retried += 1
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.warning("tasks: repair retry of %s failed", result_id, exc_info=True)
    return retried
