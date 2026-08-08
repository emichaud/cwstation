"""Status feed (reference apps.feeds integration) — incident collapsing + items."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils.timezone import now

from apps.heartbeat.feeds import StatusFeed, recent_incidents
from apps.heartbeat.models import Heartbeat, MaintenanceWindow

pytestmark = pytest.mark.django_db


def _beat(minutes_ago: int, status: str = "fail", monitor: str = "site", note: str = ""):
    return Heartbeat.objects.create(
        monitor_key=monitor,
        timestamp=now() - timedelta(minutes=minutes_ago),
        status=status,
        note=note,
    )


def test_consecutive_fails_collapse_into_one_incident():
    # Five failed beats one minute apart → a single incident.
    for m in range(5):
        _beat(minutes_ago=10 - m, note="down")
    incidents = recent_incidents()
    assert len(incidents) == 1
    assert incidents[0].beats == 5
    assert incidents[0].note == "down"


def test_gap_starts_a_new_incident():
    _beat(minutes_ago=60)          # incident 1
    _beat(minutes_ago=59)
    _beat(minutes_ago=5)           # >5 min later → incident 2
    incidents = recent_incidents()
    assert len(incidents) == 2
    # Newest first.
    assert incidents[0].started > incidents[1].started


def test_different_monitors_are_separate_incidents():
    _beat(minutes_ago=5, monitor="site")
    _beat(minutes_ago=5, monitor="api")
    assert len({i.monitor_key for i in recent_incidents()}) == 2


def test_ok_beats_are_ignored():
    _beat(minutes_ago=5, status="ok")
    assert recent_incidents() == []


def test_status_feed_merges_incidents_and_maintenance():
    _beat(minutes_ago=5, note="outage")
    MaintenanceWindow.objects.create(
        title="DB upgrade", start=now() + timedelta(days=1), end=now() + timedelta(days=1, hours=2)
    )
    items = list(StatusFeed().items(request=None))
    titles = " ".join(i.title for i in items)
    assert "Incident" in titles and "Maintenance" in titles
    # Every item carries a stable GUID and a date for reader dedupe/sorting.
    assert all(i.unique_id and i.pubdate for i in items)
