"""SmallStack feeds — first-party RSS/Atom publish + consume surface.

Publish: ``enable_rss = True`` on a CRUDView (auto feed at ``/feed/<slug>.rss``),
or ``register_feed(MyFeed())`` for a curated feed.
Consume: ``register_feed_source(name, url, …)`` + the collector (a ``@scheduled``
job and ``manage.py collect_feeds``) upserts items into a model.

See docs/skills/rss.md.
"""

from .base import Feed, FeedItem
from .registry import register_feed
from .sources import register_feed_source

__all__ = ["Feed", "FeedItem", "register_feed", "register_feed_source"]
