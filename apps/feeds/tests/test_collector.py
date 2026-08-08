"""Consume-side tests — collector upserts + dedupes into a model."""

from __future__ import annotations

import pytest

from apps.feeds import collector
from apps.feeds.collector import collect_all, collect_source
from apps.feeds.models import CollectedItem
from apps.feeds.sources import all_sources, register_feed_source, unregister_source

pytestmark = pytest.mark.django_db

SAMPLE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
  <item><title>A</title><link>https://e/a</link><guid>a</guid>
    <description>first</description><pubDate>Mon, 06 Sep 2021 16:20:00 +0000</pubDate></item>
  <item><title>B</title><link>https://e/b</link><guid>b</guid><description>second</description></item>
</channel></rss>"""


@pytest.fixture
def _fake_fetch(monkeypatch):
    """Serve SAMPLE bytes instead of hitting the network."""
    monkeypatch.setattr(collector, "_fetch", lambda url: SAMPLE)


@pytest.fixture(autouse=True)
def _clean_sources():
    yield
    for s in list(all_sources()):
        unregister_source(s.name)


def test_collect_into_bundled_model(_fake_fetch):
    register_feed_source("blog", "https://e/feed.rss")
    result = collect_source("blog")
    assert result["fetched"] == 2 and result["created"] == 2 and result["error"] is None
    assert CollectedItem.objects.filter(source="blog").count() == 2
    a = CollectedItem.objects.get(source="blog", guid="a")
    assert a.title == "A" and a.summary == "first" and a.published is not None


def test_collect_is_idempotent(_fake_fetch):
    register_feed_source("blog", "https://e/feed.rss")
    collect_source("blog")
    second = collect_source("blog")
    assert second["created"] == 0 and second["skipped"] == 2
    assert CollectedItem.objects.filter(source="blog").count() == 2


def test_same_guid_across_sources_does_not_collide(_fake_fetch):
    register_feed_source("s1", "https://e/1.rss")
    register_feed_source("s2", "https://e/2.rss")
    collect_all()
    # guid "a" exists under both sources — dedupe is scoped per source.
    assert CollectedItem.objects.filter(guid="a").count() == 2


def test_custom_model_and_mapper(_fake_fetch):
    # Map into CollectedItem with a custom shape + a different dedupe field.
    register_feed_source(
        "mapped",
        "https://e/feed.rss",
        model=CollectedItem,
        map=lambda item: {"source": "mapped", "guid": item.guid, "title": item.title.lower()},
        dedupe="guid",
    )
    collect_source("mapped")
    assert CollectedItem.objects.get(source="mapped", guid="a").title == "a"


def test_unknown_source_reports_error():
    assert collect_source("nope")["error"] == "unknown source"


def test_fetch_failure_is_contained(monkeypatch):
    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr(collector, "_fetch", boom)
    register_feed_source("bad", "https://e/feed.rss")
    result = collect_source("bad")
    assert result["created"] == 0 and "network down" in result["error"]
