"""Explorer registration for background task results, workers, and retries."""

from django.contrib import admin
from django_tasks_db.models import DBTaskResult

from apps.explorer.registry import explorer

from .models import TaskAttempt, WorkerHeartbeat


class TaskResultExplorerAdmin(admin.ModelAdmin):
    list_display = ("task_path", "queue_name", "status", "enqueued_at", "finished_at")
    explorer_readonly = True
    explorer_paginate_by = 25


class WorkerHeartbeatExplorerAdmin(admin.ModelAdmin):
    """Which db_worker processes are alive (see reaper.py for "lost")."""

    list_display = ("label", "worker_id", "hostname", "pid", "queues", "last_seen_at", "stopped_at")
    explorer_readonly = True
    explorer_paginate_by = 25


class TaskAttemptExplorerAdmin(admin.ModelAdmin):
    """Retry chains: each row links a retry to the attempt it retried."""

    list_display = (
        "result_id",
        "attempt",
        "cause",
        "root_id",
        "parent_id",
        "max_attempts",
        # Non-empty => the attempt this row replaced completed anyway, so the
        # chain executed more than once. The audit trail for a double execution.
        "parent_completed_at",
        "parent_status",
        "created_at",
    )
    explorer_readonly = True
    explorer_paginate_by = 25


explorer.register(DBTaskResult, TaskResultExplorerAdmin, group="System")
explorer.register(WorkerHeartbeat, WorkerHeartbeatExplorerAdmin, group="System")
explorer.register(TaskAttempt, TaskAttemptExplorerAdmin, group="System")
