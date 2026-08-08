"""Registry of published feeds, keyed by slug.

Two kinds resolve here:

* **Custom feeds** — registered eagerly via :func:`register_feed` by the app
  that owns them (e.g. the status feed in ``apps.heartbeat``).
* **Model feeds** — resolved *lazily* by walking ``CRUDView._registry`` for
  ``enable_rss`` views. Lazy on purpose: a CRUDView isn't registered until its
  module is imported (URLConf load), which happens after app ``ready()``. By the
  time a ``/feed/…`` request arrives, every view is registered, so lookups are
  correct without depending on app-load order.
"""

from __future__ import annotations

import logging

from .base import Feed

logger = logging.getLogger("smallstack.feeds")

_feeds: dict[str, Feed] = {}


def register_feed(feed: Feed) -> Feed | None:
    """Register a custom feed provider. First registration of a slug wins."""
    slug = getattr(feed, "slug", "")
    if not slug:
        logger.warning("Feed registration skipped — %r has no slug", feed)
        return None
    if slug in _feeds and _feeds[slug] is not feed:
        logger.warning("Feed slug %r already registered — ignoring %r", slug, feed)
        return _feeds[slug]
    _feeds[slug] = feed
    return feed


def _model_feeds() -> list[Feed]:
    """Build a ModelFeed for every ``enable_rss`` CRUDView (fresh each call —
    cheap, no DB)."""
    from apps.smallstack.crud import CRUDView

    from .model_feed import ModelFeed

    feeds: list[Feed] = []
    for view_cls in list(CRUDView._registry.values()):
        if not getattr(view_cls, "enable_rss", False):
            continue
        if getattr(view_cls, "model", None) is None:
            continue
        try:
            feeds.append(ModelFeed(view_cls))
        except Exception:
            logger.exception("Failed to build ModelFeed for %s", view_cls)
    return feeds


def get_feed(slug: str) -> Feed | None:
    """Custom feed by slug, else a model feed whose slug matches."""
    if slug in _feeds:
        return _feeds[slug]
    for feed in _model_feeds():
        if feed.slug == slug:
            return feed
    return None


def all_feeds() -> list[Feed]:
    """Every published feed — custom first, then model feeds (deduped by slug)."""
    out = list(_feeds.values())
    seen = set(_feeds)
    for feed in _model_feeds():
        if feed.slug not in seen:
            out.append(feed)
            seen.add(feed.slug)
    return out


def unregister(slug: str) -> None:
    """Test helper."""
    _feeds.pop(slug, None)
