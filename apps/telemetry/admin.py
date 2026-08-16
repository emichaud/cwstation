"""Read-only admin for captured logs.

A stopgap until the staff log viewer lands: registering here means captured
records are browsable through Django admin (and, because Explorer discovers
admin-registered models, through ``/smallstack/explorer/``) as soon as capture
is switched on.

Read-only on purpose. These rows are evidence — editing one by hand would make
the log untrustworthy, and there is no legitimate reason to.
"""

from django.contrib import admin

from .models import LogCaptureWindow, LogRecord


@admin.register(LogRecord)
class LogRecordAdmin(admin.ModelAdmin):
    list_display = ("ts", "level", "logger", "short_message", "request_id")
    list_filter = ("level", "logger")
    search_fields = ("message", "logger", "request_id", "trace_id", "exc_type")
    date_hierarchy = "ts"
    ordering = ("-ts",)

    @admin.display(description="Message")
    def short_message(self, obj: LogRecord) -> str:
        return obj.message[:120]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LogCaptureWindow)
class LogCaptureWindowAdmin(admin.ModelAdmin):
    list_display = ("level", "started_at", "expires_at", "is_active", "started_by", "note")
    list_filter = ("level",)
    ordering = ("-started_at",)

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj: LogCaptureWindow) -> bool:
        return obj.is_active
