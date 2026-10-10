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

**This receiver must never raise.** Upstream sends ``task_finished`` *inside*
its ``try`` on the success path — an exception here would flip a successful
task to ``FAILED`` — and *outside* it on the failure path, where an exception
would crash the worker loop. The repair sweep retries anything missed here.
"""

from __future__ import annotations

import logging
from typing import Any

from django.tasks import TaskResultStatus
from django.tasks.signals import task_finished
from django_tasks_db.backend import DatabaseBackend

from .reliability import maybe_retry, record_late_completion

logger = logging.getLogger(__name__)


def _from_database_worker(sender: Any) -> bool:
    """True if ``task_finished`` came from django-tasks-db's ``db_worker``.

    Upstream is inconsistent about ``sender``: its success path passes the
    backend **instance** (``task.get_backend()``, despite the variable being
    named ``backend_type``) and its failure path passes the backend **class**.
    Both forms are accepted; anything else (``ImmediateBackend`` sends its
    class) is ignored.
    """
    if isinstance(sender, DatabaseBackend):
        return True
    return isinstance(sender, type) and issubclass(sender, DatabaseBackend)


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


# django-tasks-db <= 0.12 sent its *own* bundled ``django_tasks.signals``
# instead of Django's. Subscribe to every pair present on this install (the same
# approach tracing.py takes), so a project still locked to 0.12 keeps immediate
# retries rather than silently falling back to the repair sweep. On 0.13+ the
# legacy package is gone and only Django's signal is connected.
FINISHED_SIGNALS = [task_finished]
try:
    from django_tasks.signals import task_finished as legacy_task_finished
except ImportError:
    pass
else:
    FINISHED_SIGNALS.append(legacy_task_finished)

for _signal in FINISHED_SIGNALS:
    _signal.connect(retry_failed_task, dispatch_uid="smallstack.tasks.signals.retry_failed_task")
