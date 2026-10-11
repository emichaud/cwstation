"""SmallStack's ``db_worker``: the upstream django-tasks-db worker + a heartbeat.

This deliberately **shadows** ``django_tasks_db``'s ``db_worker`` command:
``apps.tasks`` is listed before ``django_tasks_db`` in ``INSTALLED_APPS``, and
Django resolves a management command to the first app that defines it. Every
existing invocation — the Docker entrypoint's inline worker, the compose/Kamal
worker role, ``utils/dev_services.sh``, the docs — gets heartbeats and
lost-task reaping without a single deploy change. Arguments and behaviour are
otherwise identical to upstream (this subclasses it and calls straight
through).

Around the upstream worker loop it:

1. mints a per-process **incarnation** id (``label#token``, where the label is
   the operator's ``--worker-id``), hands it to upstream in place of the bare
   label so it is what ends up in ``DBTaskResult.worker_ids``, registers a
   :class:`~apps.tasks.models.WorkerHeartbeat` row for it, and runs one reaper
   pass. That pass only catches a predecessor that is *already* reapable — one
   that exited cleanly, or whose heartbeat is already older than
   ``SMALLSTACK_TASKS_LOST_AFTER_SECONDS``. A supervisor usually restarts a
   crashed worker well inside that window, so its predecessor's tasks are
   normally still too fresh to judge here and are reaped by the heartbeat
   thread's pass once the threshold elapses. The startup pass is a head start,
   not a guarantee that the queue is clean before this worker claims anything,
2. runs a :class:`~apps.tasks.heartbeat.HeartbeatThread` that refreshes it and
   periodically reaps tasks lost by *other* (dead) workers, and
3. marks the row stopped on exit, including upstream's ``sys.exit`` from its
   SIGTERM/SIGINT handler.

   A forced stop usually leaves nothing for the reaper to do: upstream catches
   its own ``sys.exit`` as a ``BaseException`` inside ``run_task`` and fails the
   row as ``builtins.SystemExit`` first, so the next pass reports
   ``running=0 reaped=0``. ``stopped_at`` matters for the cases upstream does
   *not* reach — the task thread killed by the autoreloader, or a second signal
   arriving between the row being claimed and the handler running — where it
   turns a wait for the staleness window into an immediate reap.

With ``--reload`` the parent process only supervises the autoreloader; the
heartbeat runs in the reloader child, which is where tasks execute (the same
split upstream uses for its signal handlers).

``SMALLSTACK_TASKS_HEARTBEAT_ENABLED=False`` makes this a pure pass-through to
the upstream worker. Note that this removes the reaper too, not just the
heartbeat — the thread is what schedules it — so tasks this worker loses are only
reaped if something else runs ``manage.py reap_tasks``, and then on the
no-heartbeat threshold (``SMALLSTACK_TASKS_ORPHAN_SECONDS``, a day) rather than
the 90s one.
"""

from __future__ import annotations

import logging
import os
from argparse import ArgumentTypeError
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from django.utils.autoreload import DJANGO_AUTORELOAD_ENV
from django_tasks_db.management.commands.db_worker import Command as UpstreamCommand
from django_tasks_db.management.commands.db_worker import validate_worker_id

from apps.tasks.heartbeat import HeartbeatThread, mark_stopped, new_incarnation, register_worker, safe_reap

logger = logging.getLogger("django_tasks_db")


def _queues_description(options: dict[str, Any]) -> str:
    """What the heartbeat row shows as this worker's queues.

    django-tasks-db 0.13 added ``--exclude-queues`` (only valid with
    ``--queue-name=*``). Without it in the description, a worker skipping
    ``email`` would read as serving every queue in Explorer.
    """
    queues = str(options.get("queue_name") or "")
    excluded = str(options.get("exclude_queues") or "")
    return f"{queues} (excluding {excluded})" if excluded else queues


class Command(UpstreamCommand):
    help = "Run a database background worker (SmallStack: with worker heartbeat + lost-task reaper)"

    def handle(self, *args: Any, **options: Any) -> None:
        if not getattr(settings, "SMALLSTACK_TASKS_HEARTBEAT_ENABLED", True):
            return super().handle(*args, **options)

        # Upstream disables --reload under --batch; mirror that to decide
        # which process actually runs the worker loop.
        reloading = options.get("reload") and not options.get("batch")
        if reloading and os.environ.get(DJANGO_AUTORELOAD_ENV) != "true":
            return super().handle(*args, **options)

        # configure_logging is idempotent (hasHandlers guard) and upstream calls
        # it again; calling it first makes our startup line visible.
        self.configure_logging(options.get("verbosity", 1))

        # --worker-id is the operator's *label*; what identifies the claimant has
        # to identify this *process*, or a restart under a stable label makes the
        # predecessor's abandoned task look live (and two processes sharing a
        # label make one's exit reap the other's work). So mint an incarnation and
        # hand *that* to upstream: upstream's DBTaskResult.claim() appends
        # whatever worker id it was given to worker_ids, so the reaper's
        # task → claimant join becomes exact without patching upstream.
        #
        # Validated here, not just by argparse. Upstream attaches
        # validate_worker_id as the flag's `type=`, which argparse applies to
        # command-line strings only — `call_command(..., worker_id=...)` skips
        # it entirely. That is how the unit suite and the test harness start
        # workers, so the validated path is the one humans use and the unvalidated
        # path is the one tests use: no test could ever fail on an over-long id.
        # Reuse upstream's own function so there is one rule, surfaced as a
        # CommandError rather than an argparse error raised from handle().
        try:
            label: str = validate_worker_id(str(options["worker_id"]))
        except ArgumentTypeError as exc:
            raise CommandError(f"--worker-id: {exc}") from exc
        worker_id = new_incarnation(label)
        options["worker_id"] = worker_id

        register_worker(worker_id, label, _queues_description(options))
        # Before claiming anything: fail what a crashed predecessor left RUNNING.
        safe_reap(worker_id)
        thread = HeartbeatThread(worker_id)
        thread.start()
        logger.info(
            "SmallStack db_worker: heartbeat every %ss for worker_id=%s (lost-task reaper enabled)",
            thread.interval,
            worker_id,
        )
        try:
            return super().handle(*args, **options)
        finally:
            thread.stop()
            mark_stopped(worker_id)
