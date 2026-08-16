"""Runtime control over how much gets captured to the database.

Production captures WARNING and above, so the table stays small and the write
volume stays negligible. When you need detail out of a live deployment you open
a *capture window* — "DEBUG for 15 minutes" — reproduce the problem, and the
window closes itself.

Two levels have to move for that to work, and missing the second one is the
usual reason "I turned on DEBUG and saw nothing":

1. the **handler** level — whether the database handler stores a record it is
   given; and
2. the **logger** levels — whether the record is created and dispatched to
   handlers at all. ``apps`` sits at INFO in production, so a ``logger.debug()``
   is discarded before any handler is consulted.

The window lives in the database rather than in process memory so it applies to
every worker and every container — each process polls it on its own writer
thread and applies the change locally.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# Loggers whose level we lower for the duration of a capture window. Kept to
# application code by default: lowering `django` wholesale pulls in
# django.db.backends, and a DEBUG there is one console line per SQL query.
DEFAULT_CAPTURE_LOGGERS = ["apps", "smallstack", "django.request"]

# Never let these below WARNING, whatever the window asks for or how broad the
# configured capture loggers are. django.db.backends at DEBUG emits a line per
# query — it would swamp the console handler even though the DB handler drops
# it, and on a busy request it can dominate response time.
PINNED_LOGGERS = {"django.db.backends": logging.WARNING}

# Raw `.level` values captured before we changed them, so restore is exact —
# 0 (NOTSET) must go back to 0 and keep inheriting, not become an explicit level.
_saved_levels: dict[str, int] = {}


def baseline_level() -> int:
    """The level captured when no window is open."""
    name = getattr(settings, "TELEMETRY_LOG_LEVEL", "WARNING")
    return _level_number(name)


def capture_loggers() -> list[str]:
    return list(getattr(settings, "TELEMETRY_CAPTURE_LOGGERS", DEFAULT_CAPTURE_LOGGERS))


def _level_number(name: str | int) -> int:
    """Resolve a level name to its number, falling back to WARNING.

    ``logging.getLevelName`` returns the string ``"Level foo"`` for unknown
    names rather than raising, so an unusable value must be caught here.
    """
    if isinstance(name, int):
        return name
    resolved = logging.getLevelName(str(name).upper())
    return resolved if isinstance(resolved, int) else logging.WARNING


def active_window():
    """Return the current unexpired capture window, or ``None``.

    Touches the database — call it from the writer thread, never from
    ``emit()``.
    """
    from .models import LogCaptureWindow

    return LogCaptureWindow.objects.filter(expires_at__gt=timezone.now()).order_by("-expires_at").first()


def start(level: str = "DEBUG", minutes: int = 15, actor: str = "", note: str = ""):
    """Open a capture window. Returns the created ``LogCaptureWindow``.

    Duration is clamped to ``TELEMETRY_MAX_CAPTURE_MINUTES`` (default 120) —
    an unbounded DEBUG window on a busy deployment is how you fill a disk.
    """
    from .models import LogCaptureWindow

    cap = getattr(settings, "TELEMETRY_MAX_CAPTURE_MINUTES", 120)
    minutes = max(1, min(int(minutes), cap))
    return LogCaptureWindow.objects.create(
        level=str(level).upper(),
        expires_at=timezone.now() + timedelta(minutes=minutes),
        started_by=actor[:150],
        note=note[:200],
    )


def stop() -> int:
    """Close every open window early. Returns how many were closed."""
    from .models import LogCaptureWindow

    now = timezone.now()
    return LogCaptureWindow.objects.filter(expires_at__gt=now).update(expires_at=now)


def effective_level(window=None) -> int:
    """The level that should currently be captured."""
    if window is None:
        return baseline_level()
    return min(_level_number(window.level), baseline_level())


def apply_levels(level_no: int) -> None:
    """Lower the configured loggers to ``level_no``; restore them at baseline.

    Only ever *lowers* a logger — a project that deliberately runs a logger at
    DEBUG keeps it, and a capture window at INFO won't quietly make it less
    verbose than the developer configured.
    """
    if level_no >= baseline_level():
        restore_levels()
        return

    for name in capture_loggers():
        logger = logging.getLogger(name)
        if level_no < logger.getEffectiveLevel():
            _saved_levels.setdefault(name, logger.level)
            logger.setLevel(level_no)

    for name, floor in PINNED_LOGGERS.items():
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() < floor:
            _saved_levels.setdefault(name, logger.level)
            logger.setLevel(floor)


def restore_levels() -> None:
    """Put every logger we touched back exactly as we found it."""
    while _saved_levels:
        name, original = _saved_levels.popitem()
        logging.getLogger(name).setLevel(original)
