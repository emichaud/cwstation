"""ModelFeed — a :class:`~apps.feeds.base.Feed` derived from an ``enable_rss``
CRUDView. Reuses the declarations you already made for search/the admin, so a
model gets a working feed from a single flag.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from django.http import HttpRequest

from apps.search.access import SearchAccess

from .base import Feed, FeedItem

logger = logging.getLogger("smallstack.feeds")

# Fields tried, in order, to source <pubDate> when rss_date_field is unset.
_DATE_FIELD_CANDIDATES = (
    "published_at", "published", "created_at", "created",
    "updated_at", "modified", "timestamp",
)


def _resolve(obj: Any, path: str | None) -> Any:
    """Walk a ``getattr``/``__`` path (like search's field resolution)."""
    if not path:
        return None
    value: Any = obj
    for part in path.split("__"):
        if value is None:
            return None
        value = getattr(value, part, None)
    return value


def _detail_url(view_cls, obj) -> str | None:
    """obj.get_absolute_url(), else the CRUDView ``{url_base}-detail`` route."""
    try:
        url = obj.get_absolute_url()
        if url:
            return url
    except Exception:
        pass
    from django.urls import NoReverseMatch, reverse

    url_base = getattr(view_cls, "url_base", None)
    if not url_base:
        return None
    namespace = getattr(view_cls, "namespace", None)
    name = f"{url_base}-detail"
    if namespace:
        name = f"{namespace}:{name}"
    try:
        return reverse(name, kwargs={"pk": obj.pk})
    except NoReverseMatch:
        return None


def _has_column(model, field_name: str) -> bool:
    if not field_name or "__" in field_name:
        return False
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


class ModelFeed(Feed):
    """Feed backed by a CRUDView's model. Field sources resolve at construction
    from the CRUDView's ``rss_*`` attributes, falling back to its search
    declarations."""

    def __init__(self, view_cls: type):
        self.view_cls = view_cls
        self.model = view_cls.model

        self.title_field = getattr(view_cls, "rss_title_field", None) or getattr(
            view_cls, "search_display", None
        )
        self.description_field = getattr(view_cls, "rss_description_field", None) or getattr(
            view_cls, "search_subtitle", None
        )
        self.author_field = getattr(view_cls, "rss_author_field", None)
        self.date_field = getattr(view_cls, "rss_date_field", None) or self._detect_date_field()

        self.slug = getattr(view_cls, "rss_slug", None) or self._default_slug()
        self.title = getattr(view_cls, "rss_feed_title", None) or str(
            self.model._meta.verbose_name_plural
        ).title()
        self.description = getattr(view_cls, "rss_feed_description", "") or f"Recent {self.title}."
        self.access = getattr(view_cls, "rss_access", None) or getattr(
            view_cls, "search_access", SearchAccess.STAFF
        )
        self.limit = int(getattr(view_cls, "rss_limit", 50) or 50)

        ordering = getattr(view_cls, "rss_ordering", None)
        if ordering:
            self.ordering = list(ordering)
        elif self.date_field and _has_column(self.model, self.date_field):
            self.ordering = [f"-{self.date_field}"]
        else:
            self.ordering = ["-pk"]

        # A downstream may override rss_item_extra(obj) for enclosures etc.
        self._extra_hook = getattr(view_cls, "rss_item_extra", None)

    def _default_slug(self) -> str:
        base = getattr(self.view_cls, "url_base", None)
        if base:
            return str(base).replace("/", "-")
        return f"{self.model._meta.app_label}-{self.model._meta.model_name}"

    def _detect_date_field(self) -> str | None:
        for name in _DATE_FIELD_CANDIDATES:
            if _has_column(self.model, name):
                return name
        return None

    @property
    def link(self) -> str:  # channel link → the model's list page if resolvable
        from django.urls import NoReverseMatch, reverse

        url_base = getattr(self.view_cls, "url_base", None)
        if url_base:
            try:
                return reverse(f"{url_base}-list")
            except NoReverseMatch:
                pass
        return "/"

    def items(self, request: HttpRequest) -> Iterable[FeedItem]:
        qs = self.model.objects.all().order_by(*self.ordering)[: self.limit]
        model_label = f"{self.model._meta.app_label}.{self.model.__name__}"
        # Instantiate once for the extra hook (mirrors how search calls
        # transform_hit on an instance).
        instance = None
        if callable(self._extra_hook):
            try:
                instance = self.view_cls()
            except Exception:
                instance = None

        for obj in qs:
            title = _resolve(obj, self.title_field) or str(obj)
            extra = {}
            if instance is not None:
                try:
                    extra = instance.rss_item_extra(obj) or {}
                except Exception:
                    logger.exception("rss_item_extra failed for %s", model_label)
            yield FeedItem(
                title=str(title),
                link=_detail_url(self.view_cls, obj) or "/",
                unique_id=f"{model_label}:{obj.pk}",
                description=str(_resolve(obj, self.description_field) or ""),
                pubdate=_resolve(obj, self.date_field) if self.date_field else None,
                author_name=str(_resolve(obj, self.author_field) or ""),
                extra_kwargs=extra,
            )
