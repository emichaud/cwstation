"""Feed provider primitives — the shape everything published as RSS/Atom takes.

Two ways to publish a feed:

* **Model-backed** — flip ``enable_rss = True`` on a CRUDView and a
  :class:`ModelFeed` is auto-registered, deriving each ``<item>`` from the same
  declarations search already reads (``search_display`` → title,
  ``search_subtitle`` → description, a detected timestamp → ``pubDate``, the
  detail route → ``link``). This is the "publish a list like the REST API" case.

* **Custom/curated** — subclass :class:`Feed`, implement :meth:`Feed.items`, and
  ``register_feed(MyFeed())``. Use this when a feed merges sources or is computed
  (e.g. the status page's incidents-plus-maintenance feed).

Both render through the same code (:mod:`apps.feeds.render`) and are gated by the
same access levels as search (:class:`apps.search.access.SearchAccess`).

Extension seam: :attr:`FeedItem.extra_kwargs` is passed straight to Django's
``feedgenerator.add_item`` — so a downstream app adds ``<enclosure>`` (podcasts),
``categories``, or an author simply by populating it; podcast/media support is a
downstream add-on, not core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from django.http import HttpRequest

from apps.search.access import SearchAccess


@dataclass
class FeedItem:
    """One ``<item>``/``<entry>``. ``link`` may be a path (resolved to an
    absolute URL against the request) or already absolute. ``unique_id`` is the
    stable GUID — keep it stable across renders so readers dedupe correctly."""

    title: str
    link: str
    unique_id: str
    description: str = ""
    pubdate: datetime | None = None
    updateddate: datetime | None = None
    author_name: str = ""
    categories: tuple[str, ...] = ()
    # Passed verbatim to feedgenerator.add_item — the enclosure/podcast seam.
    extra_kwargs: dict[str, Any] = field(default_factory=dict)


class Feed:
    """Base provider. Subclass and implement :meth:`items` for a curated feed."""

    slug: str = ""
    title: str = ""
    description: str = ""
    link: str = "/"  # channel link (path or absolute)
    access: str = SearchAccess.STAFF
    language: str = "en"
    # Max items rendered — a guard so a feed never dumps an unbounded table.
    limit: int = 50

    def items(self, request: HttpRequest) -> Iterable[FeedItem]:  # pragma: no cover - abstract
        raise NotImplementedError

    def channel_description(self) -> str:
        return self.description or self.title
