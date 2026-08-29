"""Telemetry app configuration."""

from django.apps import AppConfig


class TelemetryConfig(AppConfig):
    """Database-backed log capture.

    ``ready()`` eagerly starts each installed ``DatabaseLogHandler``'s
    writer/poller thread (``handlers.DatabaseLogHandler._ensure_worker``)
    instead of waiting for that handler's first log record.

    Why: a capture window is picked up by the *writer thread's* poll loop
    (``_poll_capture()``), not by ``emit()`` itself. Starting the thread on
    first record sounds equivalent but isn't, because ``emit()`` is only
    ever reached for records that already clear the handler's level —
    ``logging.Logger.callHandlers`` applies that gate *before* calling the
    handler at all. A freshly-started process sits at the WARNING baseline
    until something logs a warning-or-worse; if its early lines are all
    INFO/DEBUG (routine startup logging, a task's own ``logger.info()``),
    the writer thread — and therefore capture-window pickup — never starts,
    no matter how long a window has been open. Starting the thread here,
    unconditionally, means every process notices an open window within one
    ``poll_interval`` regardless of what it happens to log first, which is
    what ``docs/skills/logging-audit.md`` promises.

    This is safe to call from every process, including one-shot management
    commands (``migrate``, ``check``, a ``shell -c`` one-liner): it starts a
    single daemon thread and returns immediately — no database access
    happens in ``ready()`` itself, only (asynchronously, on that thread)
    once it's running. A daemon thread can't keep a short-lived process
    alive, and the thread's own guards (``handlers.py``'s missing-table and
    write-failure handling) already make an empty/mid-migration database a
    silent no-op, not noise. Sniffing ``sys.argv`` to skip this for
    "one-shot-looking" commands was considered and rejected: it would be
    guesswork (a command run via a shell script, a Celery beat entry, or an
    embedded ``call_command()`` doesn't announce itself the same way
    ``manage.py migrate`` does on the CLI), it re-introduces exactly the
    "picks it up *most* of the time" gap this fix exists to close, and the
    thing it would save — one thread + one deferred query per process — is
    already negligible next to Django's own startup cost.

    This runs after ``dictConfig`` has already built the handler: Django's
    ``django.setup()`` calls ``configure_logging()`` before
    ``apps.populate()``, so by the time this method runs, any handler
    ``LOGGING`` configured is already registered in
    ``handlers._instances`` (see ``handlers.get_handlers()``). Test settings
    disable ``LOGGING`` entirely, so ``get_handlers()`` returns nothing
    there and this is a no-op.

    ``emit()`` still calls ``_ensure_worker()`` too (see ``handlers.py``) —
    kept as a fallback, not redundant. It only matters for one specific
    deployment shape: gunicorn's default is ``preload_app = False`` (the
    shipped ``smallstack/gunicorn.conf`` doesn't set it), so each worker
    forks *before* loading the app and this ``ready()`` runs inside every
    worker. But ``preload_app = True`` is a one-line config change an
    operator might make, under which the app loads once in the master and
    workers fork afterward — a thread started here in the master does not
    survive that ``fork()``, so the lazy call in ``emit()`` is what starts
    the thread in each forked worker instead, on its first log record.

    Navigation and the staff log viewer arrive with the UI phase.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telemetry"
    verbose_name = "Telemetry"

    def ready(self) -> None:
        from apps.smallstack.navigation import nav

        nav.register(
            section="admin",
            label="Logs",
            url_name="telemetry:logs",
            icon_svg='<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M4 5h16v2H4zm0 4h10v2H4zm0 4h16v2H4zm0 4h10v2H4z"/></svg>',  # noqa: E501
            staff_required=True,
            order=15,
        )

        self._start_log_writers()

    def _start_log_writers(self) -> None:
        """Start the writer/poller thread on every installed DB log handler.

        No database access happens here — only in the thread each handler
        starts, asynchronously. See the class docstring for the full
        rationale (why this must not wait for a handler's first record, and
        why it's fine to do this unconditionally for every process type).
        """
        from .handlers import get_handlers

        for handler in get_handlers():
            handler._ensure_worker()
