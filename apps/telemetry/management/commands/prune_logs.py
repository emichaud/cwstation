"""Retention for captured log records — age first, then a hard row cap.

    */15 * * * * cd /app && python manage.py prune_logs

Two limits, because either alone fails somewhere. Age alone lets an incident
that produces a million lines in ten minutes fill the disk. A row cap alone
keeps stale records around forever on a quiet deployment. Whichever binds
first wins.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.telemetry.models import LogCaptureWindow, LogRecord


class Command(BaseCommand):
    help = "Delete captured log records beyond the retention window or row cap."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--keep-days",
            type=int,
            default=getattr(settings, "TELEMETRY_LOG_RETENTION_DAYS", 7),
            help="Delete records older than this many days.",
        )
        parser.add_argument(
            "--max-rows",
            type=int,
            default=getattr(settings, "TELEMETRY_LOG_MAX_ROWS", 20000),
            help="Hard cap on stored records; oldest beyond the cap are deleted.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Guard against nonsense (0, negative) but otherwise honour what was
        # asked for — an operator passing --max-rows 10 means 10.
        keep_days = max(1, options["keep_days"])
        max_rows = max(1, options["max_rows"])

        cutoff = timezone.now() - timedelta(days=keep_days)
        by_age, _ = LogRecord.objects.filter(ts__lt=cutoff).delete()

        by_cap = 0
        count = LogRecord.objects.count()
        if count > max_rows:
            # Find the timestamp of the oldest row worth keeping, then delete
            # past it — one indexed range delete instead of loading a million
            # pks.
            #
            # Cut on `ts`, NOT on pk. Records are queued in memory and written
            # in batches, so insertion order is not capture order: with several
            # workers writing concurrently a record captured earlier can land
            # at a higher pk. (`prune_activity` can cut on pk because its
            # timestamp is auto_now_add at insert time. This one can't.)
            #
            # Rows sharing the cutoff timestamp are all kept, so this can retain
            # slightly more than max_rows. Over-retention is the safe direction,
            # and the next run trims again.
            cutoff_ts = LogRecord.objects.order_by("-ts", "-pk").values_list("ts", flat=True)[max_rows - 1]
            by_cap, _ = LogRecord.objects.filter(ts__lt=cutoff_ts).delete()

        # Closed capture windows are a small audit trail of who turned up the
        # verbosity and why. Keep them far longer than the logs themselves.
        window_cutoff = timezone.now() - timedelta(days=max(keep_days, 90))
        LogCaptureWindow.objects.filter(expires_at__lt=window_cutoff).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"prune_logs: deleted {by_age} by age (<{cutoff:%Y-%m-%d}), "
                f"{by_cap} by row cap ({max_rows}); {LogRecord.objects.count()} remain."
            )
        )
