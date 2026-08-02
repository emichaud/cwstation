"""Help-article search integration (Phase 2)."""

from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def _is_sqlite() -> bool:
    return "sqlite" in connection.settings_dict["ENGINE"]


def _has_help_fts() -> bool:
    """Help FTS is a real indexed source on SQLite (FTS5) and Postgres (GIN)."""
    return connection.vendor in ("sqlite", "postgresql")


def test_sync_help_index_returns_count():
    from apps.help.search import sync_help_index

    count = sync_help_index()
    # Builds a real index on SQLite (FTS5) and Postgres (tsvector+GIN); other
    # engines have no help FTS table and rely on the in-memory fallback scan.
    if _has_help_fts():
        assert count >= 1
    else:
        assert count == 0


def test_search_help_articles_returns_help_hits():
    if not _has_help_fts():
        pytest.skip("Help-article FTS requires SQLite or Postgres")
    from apps.help.search import search_help_articles, sync_help_index

    sync_help_index()
    hits = search_help_articles("palette", limit=5)
    # Help docs include the palettes / theming pages — at least one should match.
    assert isinstance(hits, list)
    if hits:
        assert hits[0].model_label == "help.HelpArticle"
        assert hits[0].url is not None


def test_search_help_articles_empty_query_returns_empty():
    from apps.help.search import search_help_articles

    assert search_help_articles("") == []
    assert search_help_articles("   ") == []


def test_search_all_includes_help_hits_when_available():
    if not _has_help_fts():
        pytest.skip("Help-article FTS requires SQLite or Postgres")
    from apps.help.search import sync_help_index
    from apps.search.registry import search_all

    sync_help_index()
    hits = search_all("palette", limit_per_model=3)
    # The combined search should include at least one help-article hit.
    labels = {h.model_label for h in hits}
    assert "help.HelpArticle" in labels
