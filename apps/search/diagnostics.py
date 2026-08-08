"""Search performance diagnostics — the "is search *fast*, and if not, where?"

``search_doctor`` answers "is search *configured*." This answers the question
you actually have during a Postgres incident: **where is the time going?** It
gathers three things a locked-down prod box (no ``psql``, no shell) otherwise
hides:

* **Per-source health** — estimated rows, whether the GIN index exists, and
  the un-indexed (NULL-vector) backlog per model.
* **App-level timing** — end-to-end ``search_all`` and ``get_indexed_sources``
  plus a per-source fan-out breakdown. This is what separates a slow DB from a
  slow *app* (the help-docs re-parse hid behind fast per-model queries in the
  original incident).
* **Live query plan** — on Postgres, an ``EXPLAIN ANALYZE`` of a real FTS
  query on the biggest table, with a plain verdict: Seq Scan (bad) vs GIN
  Bitmap Index Scan (healthy), and the execution time.

``collect_diagnostics`` returns structured data; ``format_diagnostics_text``
renders the shareable report. The management command (``search_diagnose``) and
the staff web view (``/smallstack/search/diagnostics/``) both call these, so
CLI and UI never drift.
"""

from __future__ import annotations

import json
import time
from typing import Any

from django.db import connection

# Below this row count a Seq Scan is the planner's correct choice over the GIN
# index, so it should not be flagged as a problem.
_SEQ_SCAN_OK_ROWS = 1000


def collect_diagnostics(query: str | None = None) -> dict[str, Any]:
    """Gather search health + (optionally) timing/plan for ``query``."""
    from .backends import get_backend
    from .registry import all_views, get_indexed_sources, search_all

    backend = get_backend()
    vendor = connection.vendor
    views = list(all_views())

    data: dict[str, Any] = {
        "vendor": vendor,
        "backend": backend.name,
        "query": query,
        "sources": [_source_health(view, vendor) for view in views],
        "timings": {},
        "plan": None,
    }

    if query:
        t0 = time.perf_counter()
        hits = search_all(query, user=None)
        data["timings"]["search_all_ms"] = _ms(t0)
        data["timings"]["hit_count"] = len(hits)

        t1 = time.perf_counter()
        get_indexed_sources(user=None)
        data["timings"]["get_indexed_sources_ms"] = _ms(t1)

        per_source = []
        for view in views:
            t = time.perf_counter()
            try:
                n = len(backend.query(view, query, limit=10))
            except Exception:
                n = -1
            per_source.append(
                {"label": view.model_label, "ms": _ms(t), "hits": n}
            )
        data["timings"]["per_source"] = per_source

        if vendor == "postgresql":
            data["plan"] = _explain_biggest(views, query)

    return data


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _source_health(view, vendor: str) -> dict[str, Any]:
    from .registry import _display_row_count

    info: dict[str, Any] = {
        "label": view.model_label,
        "est_rows": None,
        "index": "n/a",
        "backlog": None,
    }
    try:
        info["est_rows"] = _display_row_count(view.model)
    except Exception:
        pass

    table = view.model._meta.db_table
    if vendor == "postgresql":
        from .backends.postgres_fts import _gin_index_name, _index_exists

        info["index"] = "present" if _index_exists(_gin_index_name(view)) else "MISSING"
        try:
            with connection.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM "{table}" WHERE search_vector IS NULL')
                info["backlog"] = cur.fetchone()[0]
        except Exception:
            info["backlog"] = None
    elif vendor == "sqlite":
        from .backends.sqlite_fts import _fts_table

        fts = _fts_table(view)
        try:
            with connection.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM "{fts}"')
                fts_rows = cur.fetchone()[0]
            info["index"] = "present"
            info["backlog"] = max(0, view.model.objects.count() - fts_rows)
        except Exception:
            info["index"] = "MISSING"
    return info


def _explain_biggest(views, query: str) -> dict[str, Any] | None:
    """EXPLAIN ANALYZE a real FTS query on the largest indexed table."""
    from .query_parser import to_postgres
    from .registry import _display_row_count

    candidates = []
    for view in views:
        try:
            candidates.append((_display_row_count(view.model) or 0, view))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    _, view = candidates[0]

    translated, search_type = to_postgres(query)
    if not translated:
        return None
    fn = {
        "plain": "plainto_tsquery",
        "phrase": "phraseto_tsquery",
        "raw": "to_tsquery",
    }.get(search_type, "plainto_tsquery")

    table = view.model._meta.db_table
    pk_col = view.model._meta.pk.column
    sql = (
        f"EXPLAIN (ANALYZE, FORMAT JSON) "
        f'SELECT "{pk_col}" FROM "{table}", {fn}(\'english\', %s) q '
        f"WHERE search_vector @@ q LIMIT 10"
    )
    try:
        with connection.cursor() as cur:
            cur.execute(sql, [translated])
            raw = cur.fetchone()[0]
    except Exception:
        return {
            "table": table,
            "error": "EXPLAIN failed — the search_vector column may be missing "
            "(run rebuild_search_index / check the GIN index).",
        }

    plan_doc = json.loads(raw) if isinstance(raw, str) else raw
    root = plan_doc[0] if isinstance(plan_doc, list) else plan_doc
    node_types = _collect_node_types(root.get("Plan", {}))
    uses_index = any(("Index Scan" in t or "Bitmap Index Scan" in t) for t in node_types)
    seq_scan = any(t == "Seq Scan" for t in node_types)
    est_rows = candidates[0][0]

    # A seq scan is only a problem on a *large* table. On a small one the
    # planner correctly prefers it over the index, so don't cry wolf — that
    # would flag every dev/staging DB and train people to ignore the tool.
    small_table = est_rows is not None and est_rows < _SEQ_SCAN_OK_ROWS
    if uses_index:
        verdict = "GIN Bitmap Index Scan — healthy"
        healthy = True
    elif seq_scan and small_table:
        verdict = (
            f"Seq Scan — expected at this size ({est_rows} rows); the planner "
            "picks it over the GIN index for tiny tables. Re-check on a large table."
        )
        healthy = True
    elif seq_scan:
        verdict = (
            "Seq Scan — NOT using the GIN index on a large table. Refresh stats "
            "(`manage.py analyze_search_index`); if it persists, the index is missing."
        )
        healthy = False
    else:
        verdict = "inconclusive"
        healthy = not seq_scan
    return {
        "table": table,
        "est_rows": est_rows,
        "exec_ms": round(root.get("Execution Time", 0.0), 2),
        "node_types": node_types,
        "uses_index": uses_index,
        "healthy": healthy,
        "verdict": verdict,
    }


def _collect_node_types(node: dict) -> list[str]:
    """Depth-first list of every ``Node Type`` in an EXPLAIN JSON plan tree."""
    types: list[str] = []
    if not isinstance(node, dict):
        return types
    nt = node.get("Node Type")
    if nt:
        types.append(nt)
    for child in node.get("Plans", []) or []:
        types.extend(_collect_node_types(child))
    return types


def format_diagnostics_text(data: dict[str, Any]) -> str:
    """Render a plain-text, copy-pasteable report from ``collect_diagnostics``."""
    lines: list[str] = []
    lines.append("SmallStack search diagnostics")
    lines.append("=" * 32)
    lines.append(f"engine:  {data['vendor']}")
    lines.append(f"backend: {data['backend']}")
    if data.get("query"):
        lines.append(f"query:   {data['query']!r}")
    lines.append("")

    lines.append("Per-source health")
    lines.append("-" * 32)
    lines.append(f"{'model':<34} {'est.rows':>10} {'index':>9} {'backlog':>9}")
    for s in data["sources"]:
        est = "?" if s["est_rows"] is None else str(s["est_rows"])
        backlog = "?" if s["backlog"] is None else str(s["backlog"])
        lines.append(f"{s['label']:<34} {est:>10} {s['index']:>9} {backlog:>9}")
    lines.append("")

    timings = data.get("timings") or {}
    if timings:
        lines.append("App-level timing")
        lines.append("-" * 32)
        lines.append(f"search_all:           {timings.get('search_all_ms')} ms "
                     f"({timings.get('hit_count')} hits)")
        lines.append(f"get_indexed_sources:  {timings.get('get_indexed_sources_ms')} ms")
        for row in timings.get("per_source", []):
            hits = "err" if row["hits"] < 0 else row["hits"]
            lines.append(f"  · {row['label']:<30} {row['ms']:>7} ms ({hits} hits)")
        lines.append("")

    plan = data.get("plan")
    if plan:
        lines.append("Live query plan (biggest table)")
        lines.append("-" * 32)
        lines.append(f"table:   {plan.get('table')}")
        if plan.get("error"):
            lines.append(f"error:   {plan['error']}")
        else:
            lines.append(f"exec:    {plan.get('exec_ms')} ms")
            lines.append(f"nodes:   {' → '.join(plan.get('node_types', []))}")
            lines.append(f"verdict: {plan.get('verdict')}")
        lines.append("")

    return "\n".join(lines)
