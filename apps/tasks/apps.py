"""Tasks app configuration."""

from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Configuration for the tasks app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tasks"
    verbose_name = "Background Tasks"

    def ready(self) -> None:
        # Registers the task_started/task_finished receivers that bind a
        # trace_id around every background task's execution — see
        # tracing.py's module docstring for why this is the right seam.
        from . import tracing  # noqa: F401
