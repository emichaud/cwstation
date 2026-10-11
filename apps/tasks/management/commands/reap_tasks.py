"""Fail RUNNING tasks whose worker died (manual / cron path).

Every SmallStack ``db_worker`` already runs this pass every ``REAP_EVERY`` (4)
heartbeats — about once a minute at the default 15s beat, but a quarter as often
if you quadruple ``SMALLSTACK_TASKS_HEARTBEAT_SECONDS``. So this command is for
inspection (``--dry-run``), or for deployments that raised the beat interval, or
ones whose workers run with ``SMALLSTACK_TASKS_HEARTBEAT_ENABLED=False`` — in
which case **nothing else reaps** and this needs a cron entry.
See ``apps/tasks/reaper.py``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.tasks.reaper import reap_lost_tasks


class Command(BaseCommand):
    help = "Mark RUNNING tasks whose worker has died as FAILED (WorkerLost)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the tasks that would be reaped without changing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        result = reap_lost_tasks(dry_run=dry_run)
        verb = "would reap" if dry_run else "reaped"
        for task in result.reaped:
            self.stdout.write(f"{verb} {task.result_id} {task.task_path}: {task.reason}")
        summary = f"reap_tasks{' (dry run)' if dry_run else ''}: {result}"
        self.stdout.write(self.style.SUCCESS(summary))
