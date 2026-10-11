"""Retry a failed task the moment the worker reports it (see ``reliability.py``).

Connected to Django's ``django.tasks.signals.task_finished``. Since
django-tasks-db 0.13 dropped its bundled ``django_tasks`` package, that is the
signal its ``db_worker`` sends — and it is the **same** signal every other
backend sends, ``ImmediateBackend`` (the test suite's backend) included. Through
0.12 the worker had a private signal pair and this receiver simply never heard
anything else; now it has to look at ``sender`` and act only for the database
worker (:func:`_from_database_worker`). Retrying needs ``DatabaseBackend``
anyway: it is the backend that can defer a retry and that ``DBTaskResult`` rows
belong to.

This module also owns the **severity** of a failed attempt's log record — see
:func:`log_task_outcome`. Django logs every ``FAILED`` result at ``ERROR`` with
a traceback, which for a retryable attempt describes a failure this layer is in
the middle of absorbing.

**This receiver must never raise.** Upstream sends ``task_finished`` *inside*
its ``try`` on the success path — an exception here would flip a successful
task to ``FAILED`` — and *outside* it on the failure path, where an exception
would crash the worker loop. The repair sweep retries anything missed here.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from django.conf import settings
from django.tasks import TaskResultStatus
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.signals import log_task_finished, task_finished
from django_tasks_db.backend import DatabaseBackend

from .reliability import maybe_retry, record_late_completion, retry_of

logger = logging.getLogger(__name__)
# Django's own task logger: keep using it so existing alert rules and log
# parsers keyed on "django.tasks" keep matching after we take the record over.
django_tasks_logger = logging.getLogger("django.tasks")


def _reset_sender_warning() -> None:
    """Test hook: re-arm the once-per-process unknown-sender warning."""
    _from_database_worker.warned = False  # type: ignore[attr-defined]


def _from_database_worker(sender: Any) -> bool:
    """True if ``task_finished`` came from django-tasks-db's ``db_worker``.

    Upstream is inconsistent about ``sender``: its success path passes the
    backend **instance** (``task.get_backend()``, despite the variable being
    named ``backend_type``) and its failure path passes the backend **class**.
    Both forms are accepted.

    Three outcomes, deliberately distinguished:

    * a ``DatabaseBackend`` (either form) — ours, act on it;
    * some *other* backend — a legitimate ignore, silently. ``ImmediateBackend``
      sends its class on every task in the test suite, so this must stay quiet;
    * anything else — the shape of the contract we filter on has changed. We
      still fail closed, because acting on an unrecognised sender is worse, but
      we say so **once per process**: a filter that silently stops matching
      turns retries into a no-op that no test notices, and "silently correct
      until it isn't" is precisely how the 0.12 signal change went unseen. The
      asymmetry above exists only because upstream is inconsistent; if it ever
      normalises, this line is what tells us.
    """
    if isinstance(sender, DatabaseBackend):
        return True
    if isinstance(sender, type) and issubclass(sender, DatabaseBackend):
        return True
    # A different task backend reporting its own work: not ours, not a problem.
    if isinstance(sender, BaseTaskBackend):
        return False
    if isinstance(sender, type) and issubclass(sender, BaseTaskBackend):
        return False
    if not getattr(_from_database_worker, "warned", False):
        _from_database_worker.warned = True  # type: ignore[attr-defined]
        logger.warning(
            "tasks: task_finished sender %r is not a task backend — retries will not fire for it. "
            "If django-tasks-db changed what it passes as sender, apps/tasks/signals.py "
            "needs updating; until then failed attempts fall back to the repair sweep.",
            sender if isinstance(sender, type) else type(sender),
        )
    return False


def retry_failed_task(sender: Any, task_result: Any, **kwargs: Any) -> None:
    """Record a late completion, then retry the attempt if it failed.

    Runs on *every* terminal result, not only failures, because this is the
    earliest point at which a double execution is knowable: if this attempt
    already has a retry, it was declared lost and replaced while it was still
    running, and it has now completed anyway. Both branches ran.

    The late-completion check comes first so a normally-failing attempt — which
    has no retry yet, because the retry is enqueued moments later by this very
    receiver — cannot be mistaken for one.
    """
    if not _from_database_worker(sender):
        return
    try:
        late = record_late_completion(task_result.id)
    except Exception:  # noqa: BLE001 - see module docstring; the sweep retries this
        late = None
        logger.warning("tasks: late-completion check for %s failed", getattr(task_result, "id", "?"), exc_info=True)
    try:
        if late is None and task_result.status == TaskResultStatus.FAILED:
            maybe_retry(task_result.id)
    except Exception:  # noqa: BLE001 - see module docstring
        logger.warning("tasks: retry check for %s failed", getattr(task_result, "id", "?"), exc_info=True)


def log_task_outcome(sender: Any, task_result: Any, **kwargs: Any) -> None:
    """Log a finished task, at a severity that reflects whether it was absorbed.

    Replaces Django's ``log_task_finished``, which logs **every** ``FAILED``
    result at ``ERROR`` with a traceback — including an attempt this layer is
    about to retry. The feature's whole proposition is that a transient failure
    is absorbed; logging each absorbed attempt at the severity operators page on
    means the feature's success manufactures false alarms, and no amount of
    documentation reaches an alerting pipeline. Worse, it inverts the rule the
    rest of this layer follows: a *real* signal must be recorded, so a non-event
    must not be shouted.

    So: an attempt with a retry already enqueued logs at ``WARNING`` and says
    so; a final failure keeps ``ERROR`` and the traceback. Same
    ``django.tasks`` logger and same ``Task id=… path=… state=…`` prefix, so
    existing log rules and parsers keyed on either still match.

    This runs *after* :func:`retry_failed_task` (connected first, below), which
    is what makes "is a retry already enqueued" answerable by a single indexed
    lookup rather than a re-derivation of the retry decision.
    """
    retry_id = None
    soften = getattr(settings, "SMALLSTACK_TASKS_SOFTEN_RETRYABLE_LOGS", True)
    try:
        if soften and task_result.status == TaskResultStatus.FAILED:
            retry_id = retry_of(task_result.id)
    except Exception:  # noqa: BLE001 - never raise from a receiver; fall back to ERROR
        retry_id = None
    # Sent from inside upstream's except block, so the exception is still live.
    exc_info = sys.exc_info()
    if retry_id is not None:
        django_tasks_logger.warning(
            "Task id=%s path=%s state=%s retrying_as=%s",
            task_result.id,
            task_result.task.module_path,
            task_result.status,
            retry_id,
        )
        return
    django_tasks_logger.log(
        logging.ERROR if task_result.status == TaskResultStatus.FAILED else logging.INFO,
        "Task id=%s path=%s state=%s",
        task_result.id,
        task_result.task.module_path,
        task_result.status,
        exc_info=exc_info if exc_info[0] else None,
    )


# One signal, one subscription. ``pyproject.toml`` pins django-tasks-db to
# >=0.13,<0.14, so the bundled ``django_tasks`` package of <= 0.12 cannot be
# present and there is no second signal to hear. An earlier revision subscribed
# to both "defensively": with the floor at 0.13 that branch is unreachable, so
# no test can ever exercise it — and its failure mode is a *double delivery* of
# one task to this receiver, which enqueues a retry and then immediately cancels
# it as a superseded one. Untestable defensive code against a severe silent
# failure is worse than no code.
task_finished.connect(retry_failed_task, dispatch_uid="smallstack.tasks.signals.retry_failed_task")

# Take over the finished-task log record (see log_task_outcome). Django connects
# log_task_finished at import of django.tasks.signals, i.e. before this module
# runs, so it would otherwise log ERROR before we know a retry is coming — a
# logging filter cannot fix that, the ordering is the problem. Connected *after*
# retry_failed_task so the retry already exists by the time we log.
#
# If the disconnect ever fails (Django renamed or re-registered the receiver),
# do not connect ours: two receivers would log every task twice, which is worse
# than the severity being wrong. Say so once, loudly, instead.
TOOK_OVER_TASK_LOGGING = task_finished.disconnect(log_task_finished)
if TOOK_OVER_TASK_LOGGING:
    task_finished.connect(log_task_outcome, dispatch_uid="smallstack.tasks.signals.log_task_outcome")
else:  # pragma: no cover - only reachable if Django changes its own wiring
    # Warned here *and* reported by tasks.W005, because this is a fail-safe that
    # degrades back into the exact defect it was built to fix. A warning emitted
    # once during app startup can be missed entirely — wrong log level, a worker
    # whose stderr nobody tails, a container that restarts — and then the only
    # evidence that retryable failures are paging people is the paging. A system
    # check is asked for by name on every manage.py command, so it cannot rot
    # quietly.
    logger.warning(
        "tasks: could not take over django.tasks' task_finished logger, so every retryable "
        "attempt will be logged at ERROR with a traceback even though it is being retried. "
        "Alert on scheduler run status or chain outcomes rather than on django.tasks ERROR."
    )
