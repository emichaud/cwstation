"""Automatic trace-ID correlation for background tasks.

``apps/smallstack/docs/logging-audit.md`` documents ``bind_trace_id()`` as
the correlation primitive for "background jobs, webhook delivery chains, and
multi-step pipelines [that] don't have a request ID" — the same idea as
``RequestIDMiddleware`` binding a request ID around an HTTP request, but for
async work. Nothing wired it into SmallStack's own background-task machinery,
though, so every task's log lines carried no correlation ID of any kind.

This module closes that gap generically, for every task — not just the
examples in this app — by hooking the two signals ``django_tasks_db``'s
worker already sends around each task call:
``django_tasks_db.management.commands.db_worker.Command.run_task()`` does

    task_started.send(sender=backend_type, task_result=task_result)
    ... task.call(...) ...
    task_finished.send(sender=backend_type, task_result=task_result)

(``task_finished`` fires on both the success and failure paths.) Binding on
``task_started`` and resetting on ``task_finished`` gives every task —
including ones enqueued by ``apps.scheduler``'s tick, which just calls
``task.enqueue()`` and lets the same worker machinery execute it later — a
``trace_task_<id>`` correlation ID with zero effort from the task author,
matching the "sensible defaults that just work" framing the rest of the
logging feature follows.

There are **two** identically-named signal pairs in this stack, and which one
fires depends on the configured ``TASKS`` backend:

- ``django_tasks.signals`` — the small package ``django_tasks_db`` depends on.
  ``django_tasks_db``'s ``db_worker`` is the only thing that sends these.
- ``django.tasks.signals`` — Django's own built-in Tasks framework (where
  ``apps/tasks/tasks.py``'s ``@task`` decorator comes from). Django's own
  backends, including ``ImmediateBackend``, are the only things that send
  these.

``dts.task_started is django_tasks.signals.task_started`` is ``False``: they
are different Signal objects with the same name. Subscribing to only one means
trace binding silently stops firing the moment someone changes
``TASKS["default"]["BACKEND"]`` — no exception, no warning, just an empty
``trace_id`` on every line, exactly as if this module didn't exist.
``config/settings/development.py`` offers that swap as a commented-out
one-liner, and ``config/settings/test.py`` *already* uses ImmediateBackend for
the whole suite, so the untraced case was the one being tested.

So we subscribe to both. No execution ever sends both pairs (each backend
sends exactly one), so this can't double-bind in practice — and if it ever
did, ``_stack()`` pairs binds with resets correctly anyway.
"""

from __future__ import annotations

import logging
import threading

from django_tasks.signals import task_finished as dtdb_task_finished
from django_tasks.signals import task_started as dtdb_task_started

from apps.smallstack.logging import bind_trace_id, reset_trace_id

logger = logging.getLogger(__name__)

# Django's built-in Tasks framework (Django 5.2+). Guarded rather than assumed:
# this module must not be the reason a project on an older Django fails to
# start, and the django_tasks_db path above still works without it.
try:
    from django.tasks.signals import task_finished as core_task_finished
    from django.tasks.signals import task_started as core_task_started

    _CORE_SIGNALS = [(core_task_started, core_task_finished)]
except ImportError:  # pragma: no cover - only on Django without django.tasks
    _CORE_SIGNALS = []

# A per-thread stack, not a single slot: keeps a bind always paired with its
# reset even if start/finish signals ever fire from more than one thread in
# the same process (multiple worker threads) or (defensively) nest.
_tokens = threading.local()


def _stack() -> list:
    if not hasattr(_tokens, "stack"):
        _tokens.stack = []
    return _tokens.stack


def _bind_trace_for_task(sender, task_result, **kwargs) -> None:
    """Bind a trace ID for the duration of one task's execution."""
    try:
        task_id = getattr(task_result, "id", "") or "unknown"
        token = bind_trace_id(f"trace_task_{task_id}")
        _stack().append(token)
    except Exception:  # noqa: BLE001 - tracing must never be why a task fails
        logger.debug("tasks: failed to bind trace_id for task_started", exc_info=True)


def _reset_trace_for_task(sender, task_result, **kwargs) -> None:
    """Unbind the trace ID bound in :func:`_bind_trace_for_task`."""
    try:
        stack = _stack()
        if stack:
            reset_trace_id(stack.pop())
    except Exception:  # noqa: BLE001 - tracing must never be why a task fails
        logger.debug("tasks: failed to reset trace_id for task_finished", exc_info=True)


# Connect to every signal pair that could carry a task execution — see the
# module docstring for why there is more than one. dispatch_uid makes this
# idempotent: apps.py imports this module from ready(), and a double import
# (autoreloader, a test importing it directly) must not register twice.
SIGNAL_PAIRS = [(dtdb_task_started, dtdb_task_finished), *_CORE_SIGNALS]

for _started, _finished in SIGNAL_PAIRS:
    _started.connect(_bind_trace_for_task, dispatch_uid="smallstack.tasks.tracing.bind")
    _finished.connect(_reset_trace_for_task, dispatch_uid="smallstack.tasks.tracing.reset")
