"""TelemetryConfig.ready(): starting the writer/poller thread eagerly.

This is the fix for the 2026-08-16 finding, round 6 (see
`test_smallstack_frontends/docs/findings/2026-08-16-fresh-process-misses-open-capture-window.md`).
Round 4 moved `DatabaseLogHandler._ensure_worker()` to the top of `emit()`,
before the level check, and added a regression test that called
`handler.emit(record)` directly. That could not fix the bug: the level gate
a real application hits lives in `logging.Logger.callHandlers()`, which only
calls `hdlr.handle()` (and therefore `emit()`) when
`record.levelno >= hdlr.level`. A record below a handler's current level
never reaches `emit()` via the real dispatch path, so nothing inside
`emit()` -- at any position -- can run for it. The round-4 test only passed
because it bypassed that gate by calling `emit()` directly.

The actual fix starts the writer thread from `TelemetryConfig.ready()` --
before any record has ever been logged, so it doesn't depend on one
clearing the level filter. These tests therefore drive everything through
real `logger.info()` / `logger.debug()` calls on a logger wired up the way
`LOGGING` wires up `"apps"` (permissive logger level, `propagate=False`,
the database handler attached at its WARNING baseline) -- never
`handler.emit()` / `handler.handle()` directly -- because only that real
path exercises the gate that made round 4 look complete while leaving the
bug intact.

Uses `transaction=True`: the writer thread opens its own database
connection (same reason `test_handlers.py` builds its fixture with
`start_worker=False` -- a background connection cannot see an open test
transaction). Committing for real is what lets that connection see the
capture window this test creates, and what lets this test's own queries
see what the thread writes.
"""

from __future__ import annotations

import logging
import time

import pytest
from django.apps import apps as django_apps
from django.db.utils import OperationalError

from apps.telemetry import capture
from apps.telemetry.handlers import DatabaseLogHandler
from apps.telemetry.models import LogRecord

pytestmark = pytest.mark.django_db(transaction=True)


def _wait_until(predicate, *, timeout=2.0, interval=0.02) -> bool:
    """Poll ``predicate()`` until it's true, swallowing the transient
    "database is locked" a SQLite reader can hit while the writer thread
    (a genuinely separate connection, per the module docstring) is mid
    ``bulk_create`` on the same shared in-memory test database -- that's a
    real, expected race between two live connections, not a bug, and
    retrying is exactly what a real caller polling ``log_capture status``
    against a busy SQLite deployment would do too.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
        time.sleep(interval)
    try:
        return predicate()
    except OperationalError:
        return False


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    capture.stop()
    capture.restore_levels()
    LogRecord.objects.all().delete()


def test_ready_starts_the_writer_so_a_fresh_process_notices_an_already_open_window():
    """The round-6 regression, end to end through real logger calls.

    Simulates a freshly-started process: a capture window is already open
    in the database (opened by "another process"), and a brand-new
    `DatabaseLogHandler` -- standing in for the handler `dictConfig` just
    built for this process -- is attached to a logger at its WARNING
    baseline, exactly as `LOGGING` wires up `"apps"` in dev/production
    (logger itself permissive, handler carries the baseline). Nothing has
    been logged on this handler yet.

    `TelemetryConfig._start_log_writers()` is the exact method `ready()`
    calls; invoking it here (via the app registry, not a reimplementation)
    is what a fresh process does before it ever logs a line. The first
    thing actually logged is an INFO line -- below the WARNING baseline --
    via a real `logger.info()` call, and it must still be captured.
    """
    capture.start(level="INFO", minutes=5)

    handler = DatabaseLogHandler(level="WARNING", start_worker=True, poll_interval=0.1, flush_interval=0.05)
    log = logging.getLogger("apps.round6freshprocess")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    try:
        telemetry_config = django_apps.get_app_config("telemetry")
        telemetry_config._start_log_writers()

        assert _wait_until(lambda: handler.level <= logging.INFO), (
            f"writer thread never picked up the open window; handler.level stayed at {handler.level}"
        )

        log.info("fresh process's first line, below the WARNING baseline")

        assert _wait_until(
            lambda: LogRecord.objects.filter(message__icontains="fresh process's first line").exists()
        ), "a below-baseline record from a freshly-started handler was never captured"
    finally:
        log.removeHandler(handler)
        handler.close()


def test_ready_is_a_noop_with_no_installed_handler():
    """Test settings (and any deployment with capture disabled) have no
    handler in `handlers._instances`. `_start_log_writers()` must not
    raise, touch the database, or otherwise assume a handler exists."""
    telemetry_config = django_apps.get_app_config("telemetry")
    telemetry_config._start_log_writers()  # must not raise
