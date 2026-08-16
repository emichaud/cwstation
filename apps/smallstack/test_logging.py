"""Structured logging: JSON output, request context, and settings wiring.

The production formatter used to be a ``%``-style string template that merely
*looked* like JSON::

    '{"time": "%(asctime)s", ..., "message": "%(message)s"}'

It broke in three ways, each pinned by a test below: any quote/backslash/newline
in a message produced invalid JSON; ``logger.exception()`` appended a raw
multi-line traceback after the closing brace; and ``extra={...}`` fields were
dropped on the floor. These tests are the regression fence.

They also pin the correlation contract the docs promise: a log line emitted
while handling a request carries the same ``request_id`` as the response's
``X-Request-ID`` header and the ``RequestLog`` row.
"""

import json
import logging
import logging.config

import pytest
from django.test import RequestFactory

from apps.smallstack.logging import (
    JSONFormatter,
    RequestContextFilter,
    TextFormatter,
    bind_request_id,
    bind_trace_id,
    get_request_id,
    reset_request_id,
    reset_trace_id,
)
from apps.smallstack.middleware import RequestIDMiddleware


def make_record(msg="hello", args=(), *, level=logging.INFO, exc_info=None, **extra):
    """Build a LogRecord the way logging.Logger._log() would."""
    record = logging.LogRecord(
        name="apps.tickets.views",
        level=level,
        pathname="/app/apps/tickets/views.py",
        lineno=42,
        msg=msg,
        args=args,
        exc_info=exc_info,
        func="close_ticket",
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def emit(record, formatter=None):
    """Format a record and parse the result back as JSON."""
    return json.loads((formatter or JSONFormatter()).format(record))


@pytest.fixture
def clean_context():
    """Guarantee no request/trace ID leaks between tests."""
    token = bind_request_id("")
    trace_token = bind_trace_id("")
    yield
    reset_request_id(token)
    reset_trace_id(trace_token)


# ---------------------------------------------------------------------------
# JSON validity — the bug this phase exists to fix
# ---------------------------------------------------------------------------


def test_plain_message_is_valid_json():
    payload = emit(make_record("Ticket 42 closed"))
    assert payload["message"] == "Ticket 42 closed"
    assert payload["level"] == "INFO"
    assert payload["name"] == "apps.tickets.views"
    assert payload["line"] == 42
    assert payload["func"] == "close_ticket"


@pytest.mark.parametrize(
    "message",
    [
        'Ticket "42" closed',  # double quotes closed the JSON string early
        r"Bad path C:\Users\admin",  # backslash produced an invalid escape
        "line one\nline two",  # newline split the record across lines
        "tab\there",
        'nested {"json": "payload"}',  # a serialized payload inside a message
        "unicode: café ☃",
    ],
)
def test_special_characters_survive_round_trip(message):
    """Each of these produced unparseable output under the old format string."""
    assert emit(make_record(message))["message"] == message


def test_output_is_exactly_one_line():
    record = make_record("first\nsecond\nthird")
    assert "\n" not in JSONFormatter().format(record)


def test_message_interpolation_uses_record_args():
    payload = emit(make_record("Ticket %s closed by %s", (42, "admin")))
    assert payload["message"] == "Ticket 42 closed by admin"


def test_bad_interpolation_does_not_raise():
    """A broken call site must not cost us the log line — it IS the clue."""
    payload = emit(make_record("%d items", ("seven",)))
    assert "unformattable" in payload["message"]
    assert "seven" in payload["message"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


def _exc_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        return sys.exc_info()


def test_exception_traceback_lands_inside_the_json():
    """Old behavior: the traceback was appended AFTER the closing brace."""
    rendered = JSONFormatter().format(make_record("failed", level=logging.ERROR, exc_info=_exc_info()))

    assert "\n" not in rendered  # the whole record, traceback included, is one line
    payload = json.loads(rendered)
    assert payload["exc_type"] == "ValueError"
    assert "Traceback (most recent call last)" in payload["exc"]
    assert "ValueError: boom" in payload["exc"]


def test_exception_text_is_cached_on_the_record():
    """formatException() is not cheap and every handler formats the record."""
    record = make_record("failed", level=logging.ERROR, exc_info=_exc_info())
    JSONFormatter().format(record)
    assert record.exc_text
    # Second handler reuses the cached text rather than re-rendering.
    assert emit(record)["exc"] == record.exc_text


def test_no_exception_fields_when_there_is_no_exception():
    payload = emit(make_record("all good"))
    assert "exc" not in payload
    assert "exc_type" not in payload


# ---------------------------------------------------------------------------
# extra={...} — silently discarded by the old formatter
# ---------------------------------------------------------------------------


def test_extra_fields_are_preserved():
    """Real call sites rely on this — see apps/api/threats.py, apps/help/search.py."""
    payload = emit(make_record("blocked probe", user_agent="curl/8.1", token="tok_abc"))
    assert payload["extra"] == {"user_agent": "curl/8.1", "token": "tok_abc"}


def test_no_extra_key_when_call_site_passed_none():
    assert "extra" not in emit(make_record("plain"))


def test_unserializable_extra_falls_back_to_repr():
    payload = emit(make_record("odd", thing=object()))
    assert "object object at" in payload["extra"]["thing"]


def test_context_ids_are_not_duplicated_into_extra(clean_context):
    token = bind_request_id("req_abc")
    try:
        record = make_record("hi")
        RequestContextFilter().filter(record)
        payload = emit(record)
    finally:
        reset_request_id(token)

    assert payload["request_id"] == "req_abc"
    assert "extra" not in payload


# ---------------------------------------------------------------------------
# Request / trace context
# ---------------------------------------------------------------------------


def test_filter_injects_bound_request_id(clean_context):
    token = bind_request_id("req_123")
    try:
        record = make_record()
        assert RequestContextFilter().filter(record) is True
        assert record.request_id == "req_123"
    finally:
        reset_request_id(token)


def test_filter_never_drops_records(clean_context):
    record = make_record()
    assert RequestContextFilter().filter(record) is True
    assert emit(record)["message"] == "hello"


def test_context_ids_omitted_when_unbound(clean_context):
    record = make_record()
    RequestContextFilter().filter(record)
    payload = emit(record)
    assert "request_id" not in payload
    assert "trace_id" not in payload


def test_trace_id_binds_independently(clean_context):
    token = bind_trace_id("trace_xyz")
    try:
        record = make_record()
        RequestContextFilter().filter(record)
        payload = emit(record)
    finally:
        reset_trace_id(token)

    assert payload["trace_id"] == "trace_xyz"
    assert "request_id" not in payload


def test_explicit_extra_request_id_wins_over_context(clean_context):
    """A call site that knows better (e.g. replaying a stored event) keeps its ID."""
    token = bind_request_id("req_ambient")
    try:
        record = make_record("replay", request_id="req_explicit")
        RequestContextFilter().filter(record)
    finally:
        reset_request_id(token)

    assert emit(record)["request_id"] == "req_explicit"


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def test_timestamp_is_utc_iso8601():
    """UTC with a Z suffix so lines from different hosts sort correctly."""
    time_field = emit(make_record())["time"]
    assert time_field.endswith("Z")
    assert time_field[4] == "-" and time_field[10] == "T"


# ---------------------------------------------------------------------------
# Development text formatter
# ---------------------------------------------------------------------------


def test_text_formatter_appends_request_id(clean_context):
    formatter = TextFormatter(fmt="{levelname} {name} {message}", style="{")
    token = bind_request_id("req_123")
    try:
        record = make_record("Ticket closed")
        RequestContextFilter().filter(record)
        line = formatter.format(record)
    finally:
        reset_request_id(token)

    assert line == "INFO apps.tickets.views Ticket closed request_id=req_123"


def test_text_formatter_stays_quiet_without_context(clean_context):
    formatter = TextFormatter(fmt="{levelname} {message}", style="{")
    record = make_record("startup")
    RequestContextFilter().filter(record)
    assert formatter.format(record) == "INFO startup"


# ---------------------------------------------------------------------------
# Middleware wiring
# ---------------------------------------------------------------------------


def test_middleware_binds_request_id_for_the_duration_of_the_request():
    seen = {}

    def get_response(request):
        seen["during"] = get_request_id()
        return _Response()

    response = RequestIDMiddleware(get_response)(RequestFactory().get("/"))

    assert seen["during"].startswith("req_")
    assert seen["during"] == response["X-Request-ID"]


def test_middleware_reuses_upstream_request_id():
    seen = {}

    def get_response(request):
        seen["during"] = get_request_id()
        return _Response()

    request = RequestFactory().get("/", HTTP_X_REQUEST_ID="req_from_lb")
    response = RequestIDMiddleware(get_response)(request)

    assert seen["during"] == "req_from_lb"
    assert response["X-Request-ID"] == "req_from_lb"


def test_middleware_resets_context_after_the_request():
    """WSGI threads are reused — a leak would mis-tag the next request."""
    RequestIDMiddleware(lambda request: _Response())(RequestFactory().get("/"))
    assert get_request_id() == ""


def test_middleware_resets_context_when_the_view_raises():
    def boom(request):
        raise RuntimeError("view exploded")

    with pytest.raises(RuntimeError):
        RequestIDMiddleware(boom)(RequestFactory().get("/"))

    assert get_request_id() == ""


class _Response(dict):
    """Minimal stand-in for HttpResponse — only __setitem__ is exercised."""


# ---------------------------------------------------------------------------
# Settings wiring
# ---------------------------------------------------------------------------


def load_logging_config(module_path, monkeypatch):
    """Import a settings module and hand back its LOGGING dict.

    production.py reads required env vars at import time (SECRET_KEY,
    ALLOWED_HOSTS), so supply throwaway values. Importing the module has no
    effect on django.conf.settings — the suite stays on config.settings.test.
    """
    import importlib

    monkeypatch.setenv("SECRET_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("ALLOWED_HOSTS", "example.com")
    return importlib.import_module(module_path).LOGGING


@pytest.mark.parametrize("module_path", ["config.settings.development", "config.settings.production"])
def test_settings_logging_config_is_valid(module_path, monkeypatch):
    """Catch typos in the LOGGING dicts — the test settings override them, so
    nothing else in the suite ever exercises these."""
    logging_config = load_logging_config(module_path, monkeypatch)

    # Validate formatters/filters/handlers only. Keeping "root"/"loggers" out
    # means dictConfig doesn't tear down the handlers the test session is using.
    probe = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": logging_config.get("filters", {}),
        "formatters": logging_config["formatters"],
        "handlers": {
            name: handler for name, handler in logging_config["handlers"].items() if name != "file"
        },  # the file handler would create/open LOG_FILE on disk
    }
    logging.config.dictConfig(probe)


@pytest.mark.parametrize("module_path", ["config.settings.development", "config.settings.production"])
def test_console_handler_carries_the_request_context_filter(module_path, monkeypatch):
    logging_config = load_logging_config(module_path, monkeypatch)
    assert "request_context" in logging_config["handlers"]["console"]["filters"]
