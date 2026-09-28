"""Tasks app configuration."""

from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Configuration for the tasks app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tasks"
    verbose_name = "Background Tasks"

    def ready(self) -> None:
        # tracing: registers the task_started/task_finished receivers that bind a
        # trace_id around every background task's execution — see
        # tracing.py's module docstring for why this is the right seam.
        # signals: retries a failed task as soon as db_worker reports it — see
        # reliability.py (the policy) and signals.py (why this signal).
        # checks: makes this subsystem's silent absences (no heartbeat, no
        # reaper) loud at `manage.py check` time — see checks.py.
        from . import checks, signals, tracing  # noqa: F401
