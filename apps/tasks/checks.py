"""System checks for the task-reliability subsystem.

Each of these encodes an invariant that this subsystem *silently* depends on.
They exist because the failure mode in every case is an **absence** — no
heartbeat, no reaper, no reaping — which produces no error, no log line and no
symptom until a worker dies and its task sits ``RUNNING`` for a day. A check is
the cheapest way to make a silent absence loud, and `manage.py check` runs before
almost every management command, so an operator meets it at deploy time.

None of them fire on shipped defaults. They are ``Warning``, not ``Error``: each
describes a degraded configuration that still boots and still processes tasks.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.core.checks import Warning as CheckWarning
from django.core.checks import register

# The upstream package whose db_worker command ours must shadow.
UPSTREAM_APP = "django_tasks_db"
OUR_APP = "apps.tasks"


@register()
def check_db_worker_is_not_shadowed(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """SmallStack's ``db_worker`` must be the one Django resolves.

    Django resolves a management command to the first app in ``INSTALLED_APPS``
    that defines it. If another app precedes ``apps.tasks``, its ``db_worker``
    wins and every worker silently runs without a heartbeat — tasks whose worker
    dies then wait out ``SMALLSTACK_TASKS_ORPHAN_SECONDS`` (a day) instead of 90
    seconds, with nothing anywhere saying why. Downstream forks that reorder
    ``INSTALLED_APPS`` are the realistic way to hit this.
    """
    if not django_apps.is_installed(OUR_APP) or not django_apps.is_installed(UPSTREAM_APP):
        return []
    if not getattr(settings, "SMALLSTACK_TASKS_HEARTBEAT_ENABLED", True):
        # Heartbeats are off, so our db_worker is a pure pass-through to
        # upstream's (see db_worker.Command.handle). Which command resolves then
        # makes no behavioural difference, and warning about a missing heartbeat
        # describes precisely what the operator configured on purpose. A check
        # that fires on a documented opt-out is noise, and noise in
        # `check --deploy` is how real warnings stop being read.
        return []
    from django.core.management import get_commands

    owner = get_commands().get("db_worker")
    if owner in (None, OUR_APP):
        return []
    return [
        CheckWarning(
            f"The 'db_worker' command resolves to {owner!r}, not {OUR_APP!r}, "
            "so background workers run without a heartbeat.",
            hint=(
                f"{owner!r} precedes {OUR_APP!r} in INSTALLED_APPS and defines its own db_worker. "
                f"Move {OUR_APP!r} earlier, or make your command subclass "
                "apps.tasks.management.commands.db_worker.Command so it keeps the heartbeat and "
                "lost-task reaper. Tasks whose worker dies will otherwise stay RUNNING until "
                "SMALLSTACK_TASKS_ORPHAN_SECONDS elapses."
            ),
            id="tasks.W001",
        )
    ]


@register()
def check_lost_after_is_several_beats(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """``LOST_AFTER_SECONDS`` must be several beats wide, or live tasks get reaped.

    The threshold is measured in missed heartbeats. Set it too close to the beat
    interval and ordinary write contention — a slow query, a lock held by the
    task itself — makes a *live* worker look dead, and its in-flight task is
    failed and (with a retry policy) re-run. Shipped ratio is 6x.
    """
    if not django_apps.is_installed(OUR_APP):
        return []
    beat = int(getattr(settings, "SMALLSTACK_TASKS_HEARTBEAT_SECONDS", 15))
    lost_after = int(getattr(settings, "SMALLSTACK_TASKS_LOST_AFTER_SECONDS", 90))
    if beat <= 0:
        return [
            CheckWarning(
                f"SMALLSTACK_TASKS_HEARTBEAT_SECONDS is {beat}, which is not a usable beat interval.",
                hint="Set it to a positive number of seconds (default 15).",
                id="tasks.W003",
            )
        ]
    if lost_after >= 3 * beat:
        return []
    return [
        CheckWarning(
            f"SMALLSTACK_TASKS_LOST_AFTER_SECONDS ({lost_after}s) is less than three "
            f"heartbeats (3 x {beat}s). A live worker that misses a couple of beats "
            "will have its running tasks failed as WorkerLost and possibly re-run.",
            hint=(
                "Keep the threshold several beats wide — the shipped default is 90s against a "
                "15s beat (6 beats). Either raise SMALLSTACK_TASKS_LOST_AFTER_SECONDS or lower "
                "SMALLSTACK_TASKS_HEARTBEAT_SECONDS."
            ),
            id="tasks.W002",
        )
    ]


@register()
def check_retryable_failures_are_not_logged_as_errors(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """SmallStack must own the finished-task log record, or absorbed failures page.

    ``signals.py`` disconnects Django's ``log_task_finished`` — which logs every
    ``FAILED`` result at ``ERROR`` with a traceback, including attempts being
    retried — and installs one that logs an absorbed failure at ``WARNING``. If
    that takeover fails, the fail-safe deliberately leaves Django's receiver in
    place rather than double-logging every task, which means it degrades back
    into precisely the defect it exists to fix. Silently, unless something says
    so on every command.
    """
    if not django_apps.is_installed(OUR_APP):
        return []
    from apps.tasks import signals

    if signals.TOOK_OVER_TASK_LOGGING:
        return []
    return [
        CheckWarning(
            "SmallStack could not take over django.tasks' finished-task logger, so every failed "
            "attempt is logged at ERROR with a traceback — including ones being retried.",
            hint=(
                "Django's django.tasks.signals.log_task_finished could not be disconnected, most "
                "likely because Django changed how it registers it. Until apps/tasks/signals.py is "
                "updated, a task that fails and then succeeds on retry will still raise ERRORs: "
                "alert on scheduler run status or apps.tasks.reliability.chain_outcome() rather "
                "than on django.tasks ERROR records."
            ),
            id="tasks.W005",
        )
    ]


@register()
def check_use_tz_is_enabled(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """The reaper needs aware datetimes, and fails *silently* without them.

    With ``USE_TZ = False`` the reaper's timestamp formatting raises on naive
    datetimes. Every caller swallows that exception by design (nothing may take a
    worker down), so the reaper simply never reaps, logging one warning a minute
    that nobody reads — and ``manage.py reap_tasks`` tracebacks.
    """
    if not django_apps.is_installed(OUR_APP) or getattr(settings, "USE_TZ", True):
        return []
    return [
        CheckWarning(
            "USE_TZ is False, so the lost-task reaper cannot run: it compares aware "
            "database timestamps and will raise on every pass.",
            hint=(
                "Set USE_TZ = True. Tasks whose worker dies will otherwise stay RUNNING "
                "forever, because the reaper's failures are swallowed to keep workers alive."
            ),
            id="tasks.W004",
        )
    ]
