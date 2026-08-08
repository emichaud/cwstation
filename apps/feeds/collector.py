"""Fetch → parse → upsert. The consume half of the RSS surface.

``collect_all()`` runs from both the ``@scheduled`` job and ``manage.py
collect_feeds`` (same core). Idempotent: items are deduped on the source's
``dedupe`` field, so re-polling never creates duplicates.
"""

from __future__ import annotations

import logging
import urllib.request

from .parser import ParsedItem, parse_feed
from .sources import FeedSource, all_sources, get_source

logger = logging.getLogger("smallstack.feeds")

_USER_AGENT = "SmallStack-FeedCollector/1.0 (+https://github.com/emichaud/django-smallstack)"
_TIMEOUT = 20


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 (trusted, registered URLs)
        return resp.read()


def _default_map(item: ParsedItem, source: FeedSource) -> dict:
    """ParsedItem → CollectedItem kwargs (the zero-config landing shape)."""
    return {
        "source": source.name,
        "guid": item.guid,
        "title": item.title[:500],
        "link": item.link[:1000],
        "summary": item.summary,
        "author": item.author[:255],
        "published": item.published,
        "raw": {**item.raw, "enclosures": item.enclosures} if item.enclosures else item.raw,
    }


def _target_model(source: FeedSource):
    if source.model is not None:
        return source.model
    from .models import CollectedItem

    return CollectedItem


def _model_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


def collect_source(name: str) -> dict:
    """Poll one source. Returns ``{name, fetched, created, skipped, error}``."""
    source = get_source(name)
    if source is None:
        return {"name": name, "error": "unknown source", "fetched": 0, "created": 0, "skipped": 0}
    if not source.enabled:
        return {"name": name, "error": "disabled", "fetched": 0, "created": 0, "skipped": 0}

    try:
        content = _fetch(source.url)
        items = parse_feed(content)
    except Exception as exc:
        logger.exception("Feed fetch/parse failed for %s (%s)", name, source.url)
        return {"name": name, "error": str(exc), "fetched": 0, "created": 0, "skipped": 0}

    model = _target_model(source)
    created = skipped = 0
    for item in items:
        kwargs = source.map(item) if source.map else _default_map(item, source)
        dedupe_val = kwargs.get(source.dedupe)
        if dedupe_val in (None, ""):
            skipped += 1
            continue
        lookup = {source.dedupe: dedupe_val}
        # Scope dedupe by source when the target model records it (so two
        # sources can carry the same guid without colliding).
        if "source" in kwargs and _model_has_field(model, "source"):
            lookup["source"] = kwargs["source"]
        defaults = {k: v for k, v in kwargs.items() if k not in lookup}
        try:
            _, was_created = model.objects.get_or_create(defaults=defaults, **lookup)
            created += 1 if was_created else 0
            skipped += 0 if was_created else 1
        except Exception:
            logger.exception("Upsert failed for %s item %r", name, dedupe_val)
            skipped += 1

    return {"name": name, "fetched": len(items), "created": created, "skipped": skipped, "error": None}


def collect_all() -> list[dict]:
    """Poll every enabled source. One source's failure never aborts the rest."""
    results = []
    for source in all_sources():
        if source.enabled:
            results.append(collect_source(source.name))
    return results
