"""Persisted log records and the runtime capture window.

Why a table and not a file: a container you can't shell into (Azure Container
Apps, a locked-down App Service) makes the log *stream* unreachable. Anything
in the database is reachable through the app itself — the same reason
``RequestLog`` was readable when the log stream wasn't.
"""

from django.db import models
from django.utils import timezone


class LogRecord(models.Model):
    """One captured log line.

    Field names mirror :class:`logging.LogRecord` so the mapping from a Python
    log call to a row is obvious. ``request_id`` / ``trace_id`` come from the
    context filter in ``apps.smallstack.logging`` — ``request_id`` joins to
    ``apps.activity.RequestLog``, which is where the acting user lives.
    """

    ts = models.DateTimeField(db_index=True)
    level = models.CharField(max_length=10)
    # Stored alongside `level` so range filters ("WARNING and above") are an
    # index scan rather than an IN over level names.
    level_no = models.PositiveSmallIntegerField(db_index=True)
    logger = models.CharField(max_length=200, db_index=True)
    message = models.TextField()

    module = models.CharField(max_length=200, blank=True, default="")
    func = models.CharField(max_length=200, blank=True, default="")
    line = models.PositiveIntegerField(default=0)

    request_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    trace_id = models.CharField(max_length=255, blank=True, default="", db_index=True)

    exc_type = models.CharField(max_length=200, blank=True, default="")
    exc_text = models.TextField(blank=True, default="")

    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-ts", "-pk"]
        verbose_name = "log record"
        verbose_name_plural = "log records"
        indexes = [
            # The log viewer's default query: newest first, filtered by level.
            models.Index(fields=["-ts", "level_no"], name="telemetry_ts_level_idx"),
        ]

    def __str__(self):
        return f"{self.level} {self.logger} {self.message[:80]}"


class LogCaptureWindow(models.Model):
    """A time-boxed request to capture more verbose logs than the baseline.

    Production runs at WARNING so the table stays small. When you need detail
    from a live deployment you open a window — "DEBUG for the next 15 minutes"
    — reproduce the problem, and it closes itself. Nothing to remember to turn
    off, which is what makes it safe to hand to an operator.

    Rows are append-only history; :func:`apps.telemetry.capture.active_window`
    treats the newest unexpired row as current.
    """

    LEVEL_CHOICES = [
        ("DEBUG", "Debug"),
        ("INFO", "Info"),
        ("WARNING", "Warning"),
        ("ERROR", "Error"),
    ]

    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="DEBUG")
    expires_at = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    started_by = models.CharField(max_length=150, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "log capture window"
        verbose_name_plural = "log capture windows"

    def __str__(self):
        return f"{self.level} until {self.expires_at:%Y-%m-%d %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.expires_at > timezone.now()
