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

1. registers a :class:`~apps.tasks.models.WorkerHeartbeat` row for
   ``--worker-id`` and runs one reaper pass, so tasks a crashed predecessor
   left ``RUNNING`` are failed before this worker claims anything,
2. runs a :class:`~apps.tasks.heartbeat.HeartbeatThread` that refreshes it and
   periodically reaps tasks lost by *other* (dead) workers, and
3. marks the row stopped on exit — including upstream's ``sys.exit`` from its
   SIGTERM/SIGINT handler — so a task interrupted by a forced stop is reaped
   on the next pass rather than after the staleness window.

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
from typing import Any

from django.conf import settings
from django.utils.autoreload import DJANGO_AUTORELOAD_ENV
from django_tasks_db.management.commands.db_worker import Command as UpstreamCommand

from apps.tasks.heartbeat import HeartbeatThread, mark_stopped, register_worker, safe_reap

logger = logging.getLogger("django_tasks_db")


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
        worker_id: str = options["worker_id"]
        register_worker(worker_id, options.get("queue_name", ""))
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
