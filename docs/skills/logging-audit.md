# Logging & Audit

SmallStack includes sensible logging defaults and a lightweight audit utility built on Django's `LogEntry` model.

## Logging

### Adding logging to an app

Use Python's stdlib logger with `__name__` so log output automatically includes the module path:

```python
# apps/tickets/views.py
import logging

logger = logging.getLogger(__name__)

def close_ticket(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    ticket.status = "closed"
    ticket.save()
    logger.info("Ticket %s closed by %s", ticket.pk, request.user)
```

All `apps.*` loggers are captured by the `apps` logger configured in settings.

### Attaching structured fields

Pass `extra={...}` for anything you'd want to filter or group on later. These land in the `extra` object of the production JSON output — don't concatenate them into the message string:

```python
logger.warning("Blocked probe", extra={"user_agent": ua, "score": score})
```

```json
{"time": "…", "level": "WARNING", …, "extra": {"user_agent": "curl/8.1", "score": 0.93}}
```

Keys must not collide with `LogRecord` built-ins (`name`, `module`, `message`, `args`, `levelname`, …) — Python raises `KeyError` at the call site if they do.

### Request IDs for correlation

Every request gets a unique `X-Request-ID` via `RequestIDMiddleware` (first in the middleware stack). Access it as `request.id` in views and middleware. The ID is:

- returned in the `X-Request-ID` response header,
- stored in `RequestLog.request_id`,
- and injected into **every log line emitted while handling that request**, as the `request_id` field.

So "user reported an error, here's their request ID" is a one-search answer across both the log stream and the activity log. You don't pass it anywhere — `RequestContextFilter` reads it from a `contextvar` that the middleware binds.

### Trace IDs for multi-step work

For work that spans many log lines and isn't a single HTTP request — a scheduled job, a webhook delivery chain, an agent run — bind a trace ID and every line emitted inside it carries `trace_id`:

```python
import uuid

from apps.smallstack.logging import bind_trace_id, reset_trace_id

token = bind_trace_id(f"trace_{uuid.uuid4().hex}")
try:
    run_the_multi_step_thing()   # every logger.* call in here is tagged
finally:
    reset_trace_id(token)
```

Always reset in a `finally` — worker threads are reused, and an unreset value would tag unrelated later work.

### Log levels by environment

| Logger | Development | Production |
|--------|-------------|------------|
| `django` | INFO | WARNING |
| `django.request` | DEBUG (see 4xx/5xx) | ERROR |
| `django.db.backends` | WARNING | — |
| `django.security` | INFO | WARNING |
| `apps` (your code) | DEBUG | INFO |

### Enabling SQL query logging in development

Uncomment the `django.db.backends` DEBUG logger in `config/settings/development.py`:

```python
"django.db.backends": {
    "handlers": ["console"],
    "level": "DEBUG",
    "propagate": False,
},
```

### Adjusting log levels

Override any logger in your project's settings:

```python
# In development.py or production.py
LOGGING["loggers"]["apps.tickets"] = {
    "handlers": ["console"],
    "level": "WARNING",  # Quiet a noisy app
    "propagate": False,
}
```

## Reading logs from a deployment you can't reach

Log lines are also written to the database (`apps.telemetry`), so they're readable from inside the app. This is the answer when the log *stream* isn't reachable — a locked-down container, a managed platform with no shell.

The baseline is WARNING, which keeps the table small. When you need more, open a **capture window**: it turns the level up for a fixed period and closes itself.

```bash
uv run python manage.py log_capture status
uv run python manage.py log_capture start --level DEBUG --minutes 15
uv run python manage.py log_capture stop
```

The window lives in the database, so every worker and every container picks it up within one poll interval (5s). Records are browsable at `/admin/telemetry/logrecord/` and through Explorer.

**If you turn on DEBUG and see nothing new**, the logger — not the handler — is what discarded it. A record has to be created before any handler is consulted, and `apps` sits at INFO in production. `TELEMETRY_CAPTURE_LOGGERS` lists the loggers whose level is lowered while a window is open; add yours if it isn't covered by `apps`, `smallstack`, or `django.request`.

`django.db.backends` is pinned at WARNING during capture no matter what — at DEBUG it emits one line per SQL query.

### What it costs

Nothing is written on the request path: records go onto a bounded queue and a background thread batches them out. If the app logs faster than the database absorbs it, records are dropped rather than blocking the request, and the drop is counted — `log_capture status` reports it. A non-zero `dropped` means raise `TELEMETRY_LOG_QUEUE_SIZE` or capture at a higher level.

Retention is enforced by `manage.py prune_logs` (wired into the container's cron every 15 minutes): records older than `TELEMETRY_LOG_RETENTION_DAYS` go, and a hard `TELEMETRY_LOG_MAX_ROWS` cap catches an incident that logs a million lines in ten minutes.

Set `TELEMETRY_LOG_CAPTURE_ENABLED=false` to switch the whole thing off — no handler, no queue, no thread, no rows.

## Audit with LogEntry

SmallStack provides `log_action()` and `AuditMixin` in `apps.smallstack.audit` for creating Django `LogEntry` records from non-admin code. No new models or migrations required.

### log_action()

Create an audit record manually:

```python
from apps.smallstack.audit import log_action, ADDITION, CHANGE, DELETION

# After creating an object
log_action(request.user, new_ticket, ADDITION, "Created via public form")

# After updating
log_action(request.user, ticket, CHANGE, "Escalated to priority P1")

# After deleting
log_action(request.user, ticket, DELETION)
```

### AuditMixin

Automatically log create/update actions in class-based views:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, UpdateView

from apps.smallstack.audit import AuditMixin


class TicketCreateView(AuditMixin, LoginRequiredMixin, CreateView):
    model = Ticket
    fields = ["title", "description", "priority"]


class TicketUpdateView(AuditMixin, LoginRequiredMixin, UpdateView):
    model = Ticket
    fields = ["status", "priority", "assigned_to"]
```

The mixin detects create vs update and logs which fields changed. Override `get_audit_message(form)` to customize:

```python
class TicketUpdateView(AuditMixin, LoginRequiredMixin, UpdateView):
    model = Ticket
    fields = ["status"]

    def get_audit_message(self, form):
        if "status" in form.changed_data:
            return f"Status changed to {form.instance.status}"
        return super().get_audit_message(form)
```

### Browsing audit logs

LogEntry is registered in Django admin at `/admin/admin/logentry/`. It shows all actions from both admin and `log_action()` calls, with filters for action type, content type, and user.
