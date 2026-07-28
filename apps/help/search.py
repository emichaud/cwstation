"""Help-article search adapter.

Bridges the existing markdown-based help system (``apps/help/utils.py``)
into the unified search backend. Help articles aren't Django models —
they're markdown files on disk — so they live as a separate "source"
inside the search system, queried alongside CRUDView results.

The shape of the help-article search table mirrors the FTS5 model
tables but with a fixed schema (slug, section, title, text). Backend
selection follows the same engine-detection logic as model search.
"""

from __future__ import annotations

import logging

from apps.search.backends.base import SearchHit

logger = logging.getLogger("smallstack.search.help")

# Single virtual table for help articles. Keyed by (section, slug) so
# the same slug can exist in different sections without colliding.
HELP_FTS_TABLE = "help_articles_search_idx"


def sync_help_index() -> int:
    """Rebuild the help-article search index from filesystem markdown.

    Returns the article count indexed. Idempotent — drops and refills
    the table. Cheap (under ~100 articles in a typical install).
    """
    from django.db import connection

    from apps.help.utils import build_search_index

    engine = connection.settings_dict["ENGINE"]

    if "sqlite" not in engine:
        # PG-FTS for help docs ships in a follow-up; for now help search
        # is SQLite-only. Fallback would scan every article on every
        # query which is fine for ~100 articles but not principled.
        logger.info(
            "Help search index sync skipped on %s — SQLite-only in v0.11.0", engine
        )
        return 0

    articles = build_search_index()
    if not articles:
        return 0

    with connection.cursor() as cur:
        cur.execute(
            f'CREATE VIRTUAL TABLE IF NOT EXISTS "{HELP_FTS_TABLE}" USING fts5'
            f'("slug" UNINDEXED, "section" UNINDEXED, "title", "text",'
            f' tokenize="porter unicode61")'
        )
        cur.execute(f'DELETE FROM "{HELP_FTS_TABLE}"')
        for article in articles:
            cur.execute(
                f'INSERT INTO "{HELP_FTS_TABLE}" ("slug", "section", "title", "text") '
                f"VALUES (%s, %s, %s, %s)",
                [
                    article.get("slug", ""),
                    article.get("section", ""),
                    article.get("title", ""),
                    article.get("text", ""),
                ],
            )
    return len(articles)


_HELP_INDEX_BUILT = False


def _ensure_help_index() -> None:
    """Lazily populate the help-article index on first query.

    Saves the per-boot cost of always running sync_help_index() in
    HelpConfig.ready() — tests don't pay it; production pays it once
    on the first call. The management command sync_help_index forces
    a rebuild.
    """
    global _HELP_INDEX_BUILT
    if _HELP_INDEX_BUILT:
        return
    if help_article_count() > 0:
        _HELP_INDEX_BUILT = True
        return
    try:
        sync_help_index()
    except Exception:
        logger.exception("Lazy help-index sync failed")
    _HELP_INDEX_BUILT = True


def search_help_articles(query: str, limit: int = 10) -> list[SearchHit]:
    """Query the help-article index. Returns SearchHits with help URLs."""
    from django.db import connection

    from apps.search.query_parser import to_fts5

    if "sqlite" not in connection.settings_dict["ENGINE"]:
        return _fallback_scan(query, limit)

    _ensure_help_index()

    translated = to_fts5(query)
    if not translated:
        return []

    sql = (
        f'SELECT slug, section, title, snippet("{HELP_FTS_TABLE}", 3, "", "", "…", 12) AS snip, '
        f'-bm25("{HELP_FTS_TABLE}", 0.0, 0.0, 3.0, 1.0) AS rank '
        f'FROM "{HELP_FTS_TABLE}" WHERE "{HELP_FTS_TABLE}" MATCH %s '
        f"ORDER BY rank DESC LIMIT %s"
    )
    try:
        with connection.cursor() as cur:
            cur.execute(sql, [translated, limit])
            rows = cur.fetchall()
    except Exception:
        logger.exception("Help search query failed: %r", query)
        return []

    hits: list[SearchHit] = []
    for slug, section, title, snip, rank in rows:
        url = _resolve_help_url(slug, section)
        hits.append(SearchHit(
            model_label="help.HelpArticle",
            model_verbose="Help & Docs",
            object_id=0,
            display=title or slug,
            subtitle=section,
            snippet=snip or "",
            url=url,
            rank=float(rank),
        ))
    return hits


def _fallback_scan(query: str, limit: int) -> list[SearchHit]:
    """In-memory scan for non-SQLite databases. Cheap at ~100 articles."""
    from apps.help.utils import build_search_index

    q = query.lower().strip()
    if not q:
        return []

    hits: list[SearchHit] = []
    for article in build_search_index():
        title = article.get("title", "") or ""
        text = article.get("text", "") or ""
        score = 0
        if q in title.lower():
            score += 3
        if q in text.lower():
            score += 1
        if score:
            url = _resolve_help_url(article.get("slug", ""), article.get("section", ""))
            hits.append(SearchHit(
                model_label="help.HelpArticle",
                model_verbose="Help & Docs",
                object_id=0,
                display=title or article.get("slug", ""),
                subtitle=article.get("section", ""),
                snippet=_extract_window(text, q),
                url=url,
                rank=float(score),
            ))
    hits.sort(key=lambda h: h.rank, reverse=True)
    return hits[:limit]


def _resolve_help_url(slug: str, section: str) -> str | None:
    from django.urls import NoReverseMatch, reverse

    try:
        if section:
            return reverse("help:section_detail", kwargs={"section": section, "slug": slug})
        return reverse("help:detail", kwargs={"slug": slug})
    except NoReverseMatch:
        return None


def _extract_window(text: str, q: str) -> str:
    lower = text.lower()
    idx = lower.find(q)
    if idx < 0:
        return text[:160]
    start = max(0, idx - 40)
    end = min(len(text), idx + 120)
    out = text[start:end]
    if start > 0:
        out = "…" + out
    if end < len(text):
        out = out + "…"
    return out


def help_article_count() -> int:
    """Number of articles currently indexed."""
    from django.db import connection

    if "sqlite" not in connection.settings_dict["ENGINE"]:
        from apps.help.utils import build_search_index
        return len(build_search_index())
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{HELP_FTS_TABLE}"')
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Lexical RAG — passage-level (chunk) index over the SAME help markdown.
#
# HELP_FTS_TABLE (above) holds one row per ARTICLE — good for "take me to the
# page." This table holds one row per markdown SECTION so a tool/LLM gets a
# focused, cited PASSAGE instead of a whole document. Same FTS5 engine, same
# SearchHit output shape, zero extra dependencies.
#
# The semantic half of RAG (embeddings + vector KNN) is intentionally NOT here.
# It can be layered on later behind the same SearchHit contract — callers of
# search_help_chunks() can't tell lexical from semantic, which is exactly what
# lets that upgrade happen without touching downstream code.
# ---------------------------------------------------------------------------

HELP_CHUNK_TABLE = "help_chunks_search_idx"


def sync_help_rag_index() -> int:
    """Rebuild the passage-level help index from filesystem markdown.

    Returns the number of chunks indexed. Idempotent — drops and refills the
    table. SQLite-only for now, mirroring sync_help_index().
    """
    from django.db import connection

    from apps.help.chunking import chunk_markdown, iter_help_markdown

    engine = connection.settings_dict["ENGINE"]
    if "sqlite" not in engine:
        logger.info("Help RAG index sync skipped on %s — SQLite-only", engine)
        return 0

    count = 0
    with connection.cursor() as cur:
        # Columns: source(0) section(1) heading_path(2) text(3).
        # source/section are UNINDEXED — carried for URL resolution only.
        cur.execute(
            f'CREATE VIRTUAL TABLE IF NOT EXISTS "{HELP_CHUNK_TABLE}" USING fts5'
            f'("source" UNINDEXED, "section" UNINDEXED, "heading_path", "text",'
            f' tokenize="porter unicode61")'
        )
        cur.execute(f'DELETE FROM "{HELP_CHUNK_TABLE}"')
        for doc in iter_help_markdown():
            for ch in chunk_markdown(doc["text"], source=doc["source"]):
                cur.execute(
                    f'INSERT INTO "{HELP_CHUNK_TABLE}" '
                    f'("source", "section", "heading_path", "text") '
                    f"VALUES (%s, %s, %s, %s)",
                    [doc["source"], doc["section"], ch["heading_path"], ch["text"]],
                )
                count += 1
    return count


_HELP_RAG_INDEX_BUILT = False


def _ensure_help_rag_index() -> None:
    """Lazily populate the chunk index on first query (mirrors _ensure_help_index)."""
    global _HELP_RAG_INDEX_BUILT
    if _HELP_RAG_INDEX_BUILT:
        return
    if help_chunk_count() > 0:
        _HELP_RAG_INDEX_BUILT = True
        return
    try:
        sync_help_rag_index()
    except Exception:
        logger.exception("Lazy help-RAG-index sync failed")
    _HELP_RAG_INDEX_BUILT = True


def help_chunk_count() -> int:
    """Number of passages currently indexed."""
    from django.db import connection

    if "sqlite" not in connection.settings_dict["ENGINE"]:
        return 0
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{HELP_CHUNK_TABLE}"')
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def search_help_chunks(query: str, limit: int = 5) -> list[SearchHit]:
    """Query the passage index. Returns SearchHits citing the source section.

    Same output contract as search_help_articles() — the display line is the
    heading trail (the citation), the snippet is the matched passage text. A
    future semantic tier returns the same SearchHit shape, so the MCP tool,
    API, and UI never branch on which engine answered.
    """
    from django.db import connection

    from apps.search.query_parser import to_fts5

    if "sqlite" not in connection.settings_dict["ENGINE"]:
        return []

    _ensure_help_rag_index()

    translated = to_fts5(query)
    if not translated:
        return []

    # Weight heading_path (col 2) above body text (col 3) in bm25; the two
    # UNINDEXED columns get 0.0. snippet() pulls from the text column (index 3).
    sql = (
        f'SELECT source, section, heading_path, '
        f'snippet("{HELP_CHUNK_TABLE}", 3, "", "", "…", 24) AS snip, '
        f'-bm25("{HELP_CHUNK_TABLE}", 0.0, 0.0, 2.0, 1.0) AS rank '
        f'FROM "{HELP_CHUNK_TABLE}" WHERE "{HELP_CHUNK_TABLE}" MATCH %s '
        f"ORDER BY rank DESC LIMIT %s"
    )
    try:
        with connection.cursor() as cur:
            cur.execute(sql, [translated, limit])
            rows = cur.fetchall()
    except Exception:
        logger.exception("Help RAG search query failed: %r", query)
        return []

    hits: list[SearchHit] = []
    for source, section, heading_path, snip, rank in rows:
        url = _resolve_help_url(source, section)
        hits.append(SearchHit(
            model_label="help.HelpChunk",
            model_verbose="Help & Docs",
            object_id=0,
            display=heading_path or source,
            subtitle=source,
            snippet=snip or "",
            url=url,
            rank=float(rank),
            extra={"source": source, "section": section, "heading_path": heading_path},
        ))
    return hits
