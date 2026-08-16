"""Capture windows: opening, expiry, and the logger-level surgery.

The subtle half is `apply_levels` / `restore_levels`. Turning capture up means
mutating global logging state, and getting the restore wrong leaves a
deployment permanently more (or less) verbose than its settings say — a bug
that outlives the window that caused it.
"""

import logging
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.telemetry import capture
from apps.telemetry.models import LogCaptureWindow

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def restore_logger_levels():
    """No test may leak a lowered logger into the next one."""
    yield
    capture.restore_levels()


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------


def test_start_opens_a_window():
    window = capture.start(level="DEBUG", minutes=15, actor="admin", note="chasing a 500")

    assert window.is_active
    assert capture.active_window() == window
    assert window.started_by == "admin"
    assert window.note == "chasing a 500"


def test_expired_windows_are_not_active():
    LogCaptureWindow.objects.create(level="DEBUG", expires_at=timezone.now() - timedelta(seconds=1))
    assert capture.active_window() is None


def test_newest_window_wins():
    LogCaptureWindow.objects.create(level="INFO", expires_at=timezone.now() + timedelta(minutes=5))
    later = LogCaptureWindow.objects.create(level="DEBUG", expires_at=timezone.now() + timedelta(minutes=30))

    assert capture.active_window() == later


def test_stop_closes_every_open_window():
    capture.start(minutes=15)
    capture.start(minutes=30)

    assert capture.stop() == 2
    assert capture.active_window() is None


@override_settings(TELEMETRY_MAX_CAPTURE_MINUTES=30)
def test_duration_is_clamped():
    """An unattended DEBUG window is how you fill a disk."""
    window = capture.start(minutes=10_000)
    assert (window.expires_at - timezone.now()).total_seconds() <= 30 * 60 + 5


def test_duration_has_a_floor():
    window = capture.start(minutes=0)
    assert window.expires_at > timezone.now()


# ---------------------------------------------------------------------------
# Effective level
# ---------------------------------------------------------------------------


@override_settings(TELEMETRY_LOG_LEVEL="WARNING")
def test_effective_level_without_a_window_is_the_baseline():
    assert capture.effective_level(None) == logging.WARNING


@override_settings(TELEMETRY_LOG_LEVEL="WARNING")
def test_effective_level_with_a_window_is_the_window():
    window = capture.start(level="DEBUG", minutes=5)
    assert capture.effective_level(window) == logging.DEBUG


@override_settings(TELEMETRY_LOG_LEVEL="DEBUG")
def test_a_window_never_captures_less_than_the_baseline():
    """A window is a request for *more* detail. One asking for ERROR against a
    DEBUG baseline must not quietly turn capture down."""
    window = capture.start(level="ERROR", minutes=5)
    assert capture.effective_level(window) == logging.DEBUG


@override_settings(TELEMETRY_LOG_LEVEL="nonsense")
def test_unparseable_baseline_falls_back_to_warning():
    assert capture.baseline_level() == logging.WARNING


# ---------------------------------------------------------------------------
# Logger level surgery
# ---------------------------------------------------------------------------


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest"])
def test_apply_levels_lowers_the_configured_loggers():
    """The half people miss: a record has to be created before any handler
    sees it, so the logger has to come down too."""
    logger = logging.getLogger("apps.captest")
    logger.setLevel(logging.INFO)

    capture.apply_levels(logging.DEBUG)

    assert logger.level == logging.DEBUG


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest"])
def test_restore_puts_the_original_level_back():
    logger = logging.getLogger("apps.captest")
    logger.setLevel(logging.INFO)

    capture.apply_levels(logging.DEBUG)
    capture.restore_levels()

    assert logger.level == logging.INFO


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest_notset"])
def test_restore_returns_an_inheriting_logger_to_inheriting():
    """NOTSET must go back to NOTSET, not to whatever it was inheriting —
    otherwise the window permanently pins a level the project never set."""
    logger = logging.getLogger("apps.captest_notset")
    logger.setLevel(logging.NOTSET)

    capture.apply_levels(logging.DEBUG)
    assert logger.level == logging.DEBUG

    capture.restore_levels()
    assert logger.level == logging.NOTSET


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest_verbose"])
def test_apply_levels_never_raises_a_logger():
    """A project deliberately running a logger at DEBUG keeps it."""
    logger = logging.getLogger("apps.captest_verbose")
    logger.setLevel(logging.DEBUG)

    capture.apply_levels(logging.INFO)

    assert logger.level == logging.DEBUG


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest"])
def test_repeated_apply_does_not_lose_the_original_level():
    """The writer thread re-applies on every poll — the saved level must be the
    one from before the *first* apply, not from the previous poll."""
    logger = logging.getLogger("apps.captest")
    logger.setLevel(logging.INFO)

    capture.apply_levels(logging.DEBUG)
    capture.apply_levels(logging.DEBUG)
    capture.apply_levels(logging.DEBUG)
    capture.restore_levels()

    assert logger.level == logging.INFO


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["django"])
def test_sql_logging_is_pinned_even_when_the_whole_django_tree_is_captured():
    """django.db.backends at DEBUG is one line per query. It would swamp the
    console handler even though the database handler excludes it."""
    backends = logging.getLogger("django.db.backends")
    backends.setLevel(logging.NOTSET)

    capture.apply_levels(logging.DEBUG)

    assert backends.getEffectiveLevel() >= logging.WARNING

    capture.restore_levels()
    assert backends.level == logging.NOTSET


@override_settings(TELEMETRY_LOG_LEVEL="WARNING", TELEMETRY_CAPTURE_LOGGERS=["apps.captest"])
def test_applying_the_baseline_restores_instead_of_lowering():
    """How a closing window unwinds itself: apply_levels(baseline) is the
    signal that there is nothing to capture beyond normal."""
    logger = logging.getLogger("apps.captest")
    logger.setLevel(logging.INFO)

    capture.apply_levels(logging.DEBUG)
    capture.apply_levels(logging.WARNING)  # window closed; back to baseline

    assert logger.level == logging.INFO
