"""Event-filter pattern picker (F-015): a UI upgrade over the raw-JSON textarea that
serializes to the same JSON list, backward-compatible."""

from __future__ import annotations

import json

import pytest
from django.http import QueryDict

from apps.webhooks import services
from apps.webhooks.views import EventFilterWidget, WebhookReceiverCRUDView

pytestmark = pytest.mark.django_db


def test_widget_combines_checkboxes_and_extra():
    w = EventFilterWidget()
    data = QueryDict(mutable=True)
    data.setlist("event_filter_choice", ["*.created", "*.updated"])
    data["event_filter_extra"] = "support.ticket.*\ncustom.thing.created"
    value = w.value_from_datadict(data, {}, "event_filter")
    assert json.loads(value) == [
        "*.created", "*.updated", "support.ticket.*", "custom.thing.created",
    ]


def test_widget_dedupes():
    w = EventFilterWidget()
    data = QueryDict(mutable=True)
    data.setlist("event_filter_choice", ["*", "*"])
    data["event_filter_extra"] = "*"
    assert json.loads(w.value_from_datadict(data, {}, "event_filter")) == ["*"]


def test_widget_empty_is_empty_list():
    w = EventFilterWidget()
    data = QueryDict(mutable=True)
    assert json.loads(w.value_from_datadict(data, {}, "event_filter")) == []


def test_widget_renders_current_and_custom():
    w = EventFilterWidget()
    html = w.render("event_filter", ["*.created", "my.custom.pattern"])
    # A known option is rendered checked; an unknown one lands in the extra textarea.
    assert "checked" in html
    assert "my.custom.pattern" in html


def test_available_events_includes_opted_in_model():
    """available_events surfaces the concrete events an enable_webhooks model emits."""
    original = WebhookReceiverCRUDView.enable_webhooks
    WebhookReceiverCRUDView.enable_webhooks = True
    try:
        events = services.available_events()
    finally:
        WebhookReceiverCRUDView.enable_webhooks = original
    assert "*" in events
    assert "webhooks.webhookreceiver.*" in events
    assert "webhooks.webhookreceiver.created" in events
