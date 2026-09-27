"""Exception classes recorded on task results by SmallStack's task layer."""

from __future__ import annotations


class WorkerLost(Exception):
    """Recorded as the exception class of a task whose worker died mid-run.

    Never raised — it exists so ``exception_class_path`` on a reaped
    ``DBTaskResult`` names an importable class (see ``reaper.py``).
    """


WORKER_LOST_PATH = f"{WorkerLost.__module__}.{WorkerLost.__qualname__}"

# Recorded by upstream's worker when a second SIGTERM/SIGINT force-exits it
# mid-task (``sys.exit`` inside the task call is caught as BaseException).
INTERRUPTED_PATHS = frozenset({"builtins.SystemExit", "builtins.KeyboardInterrupt"})
