"""Exception classes recorded on task results by SmallStack's task layer."""

from __future__ import annotations


class WorkerLost(Exception):
    """Recorded as the exception class of a task whose worker died mid-run.

    Never raised — it exists so ``exception_class_path`` on a reaped
    ``DBTaskResult`` names an importable class (see ``reaper.py``).
    """


class RetrySuperseded(Exception):
    """Recorded on a retry cancelled because the attempt it replaced completed.

    Never raised. ``django-tasks-db`` has no cancelled state (only READY,
    RUNNING, FAILED, SUCCESSFUL), so a retry that is no longer wanted is failed
    with this class rather than deleted — keeping it visible in the Explorer and
    in its chain. Best-effort only: the cancel is a conditional UPDATE on
    ``status=READY``, so a retry that has already started is left completely
    alone, and cancelling one that already ran would not un-execute it.
    """


WORKER_LOST_PATH = f"{WorkerLost.__module__}.{WorkerLost.__qualname__}"
RETRY_SUPERSEDED_PATH = f"{RetrySuperseded.__module__}.{RetrySuperseded.__qualname__}"

# Recorded by upstream's worker when a second SIGTERM/SIGINT force-exits it
# mid-task (``sys.exit`` inside the task call is caught as BaseException).
INTERRUPTED_PATHS = frozenset({"builtins.SystemExit", "builtins.KeyboardInterrupt"})
