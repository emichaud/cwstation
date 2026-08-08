"""Registry of feed sources to consume.

A downstream app declares its sources in ``apps.py:ready()`` (or a
``feed_sources.py`` autodiscovered module)::

    from apps.feeds import register_feed_source

    register_feed_source("upstream-status", "https://status.vendor.com/feed.rss")

    # or map into your own model:
    register_feed_source(
        "vendor-incidents",
        "https://status.vendor.com/history.atom",
        model=Incident,
        map=lambda item: {"guid": item.guid, "headline": item.title,
                          "url": item.link, "opened_at": item.published},
        dedupe="guid",
    )

The collector polls every enabled source and upserts (deduped on ``dedupe``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("smallstack.feeds")


@dataclass
class FeedSource:
    name: str
    url: str
    model: type | None = None  # None → the bundled CollectedItem
    map: Callable[[Any], dict] | None = None  # ParsedItem -> model kwargs
    dedupe: str = "guid"  # unique field (scoped per-source) to detect seen items
    enabled: bool = True


_sources: dict[str, FeedSource] = {}


def register_feed_source(
    name: str,
    url: str,
    *,
    model: type | None = None,
    map: Callable[[Any], dict] | None = None,
    dedupe: str = "guid",
    enabled: bool = True,
) -> FeedSource:
    """Register (or replace) a feed source by ``name``."""
    if name in _sources:
        logger.info("Feed source %r re-registered", name)
    source = FeedSource(name=name, url=url, model=model, map=map, dedupe=dedupe, enabled=enabled)
    _sources[name] = source
    return source


def get_source(name: str) -> FeedSource | None:
    return _sources.get(name)


def all_sources() -> list[FeedSource]:
    return list(_sources.values())


def unregister_source(name: str) -> None:
    """Test helper."""
    _sources.pop(name, None)
