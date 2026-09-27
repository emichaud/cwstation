"""Retry a failed task the moment the worker reports it (see ``reliability.py``).

Connected to ``django_tasks.signals.task_finished`` — the pair only
django-tasks-db's ``db_worker`` sends. ``ImmediateBackend`` (the test suite's
backend) sends ``django.tasks.signals`` instead and can't defer a retry anyway,
so it is deliberately not connected.

**This receiver must never raise.** Upstream sends ``task_finished`` *inside*
its ``try`` on the success path — an exception here would flip a successful
task to ``FAILED`` — and *outside* it on the failure path, where an exception
would crash the worker loop. The repair sweep retries anything missed here.
"""

from __future__ import annotations

import logging
from typing import Any

from django_tasks.base import TaskResultStatus
from django_tasks.signals import task_finished

from .reliability import maybe_retry

logger = logging.getLogger(__name__)


def retry_failed_task(sender: Any, task_result: Any, **kwargs: Any) -> None:
    try:
        if task_result.status == TaskResultStatus.FAILED:
            maybe_retry(task_result.id)
    except Exception:  # noqa: BLE001 - see module docstring
        logger.warning("tasks: retry check for %s failed", getattr(task_result, "id", "?"), exc_info=True)


task_finished.connect(retry_failed_task, dispatch_uid="smallstack.tasks.signals.retry_failed_task")
