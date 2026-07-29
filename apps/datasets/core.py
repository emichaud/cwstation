"""The Dataset wrapper: schema / rows / series over a registered queryset.

Everything here is *composed* from the existing CRUDView list pipeline — it does
not reinvent filtering, serialization, or type derivation:

- typed columns  → ``_model_field_type`` (api.py) + ``_build_filter_meta`` (crud.py)
- sub-filtering  → ``apply_filters`` (crud.py, the dependency-free path MCP reuses)
- ordering       → ``apply_ordering`` (crud.py)
- row → dict     → ``serialize`` (api.py)
- series rollup  → ``qs.values(dim).annotate(agg)`` (same idea as _compute_aggregations)

A ``Dataset`` isn't a CRUDView, so a small adapter (``_ConfigAdapter``) presents
exactly the ``crud_config`` interface those helpers expect (``.model``,
``_resolve_filter_fields``, ``_resolve_search_fields``, ``_get_list_fields``).
This is the same trick ``apps/mcp/factory.py`` uses to reuse the pipeline
outside HTTP.
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

from django.http import HttpRequest, QueryDict

from .registry import DatasetDef, all_defs, get_def

# Types the framework classifies as numeric → chartable "measures".
_MEASURE_TYPES = {"integer", "float", "decimal"}
_SERIES_LIMIT_MAX = 500
_ROWS_LIMIT_MAX = 500


def _fake_request(user=None, query: str = "") -> HttpRequest:
    """Minimal request so the pipeline helpers can read ``.user`` / ``.GET``."""
    req = HttpRequest()
    req.method = "GET"
    req.user = user
    req.GET = QueryDict(query, mutable=False)
    req.META = {}
    return req


def _label_for(model, name: str) -> str:
    try:
        return str(model._meta.get_field(name).verbose_name).capitalize()
    except Exception:
        return name.replace("_", " ").capitalize()


def _series_label(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Dataset:
    """Runtime wrapper around a registered ``DatasetDef``."""

    def __init__(self, dfn: DatasetDef):
        self.dfn = dfn
        self.key = dfn.key

    # -- queryset / model ----------------------------------------------------

    def _queryset(self, request=None):
        """Build the source queryset (lazy — no DB hit)."""
        fn = self.dfn.fn
        try:
            takes_arg = len(inspect.signature(fn).parameters) >= 1
        except (TypeError, ValueError):
            takes_arg = False
        return fn(request) if takes_arg else fn()

    @property
    def model(self):
        return self._queryset().model

    # -- columns (DB-free) ---------------------------------------------------

    def columns(self) -> list[tuple[str, str]]:
        """Resolve ``[(name, type)]``. No DB access."""
        model = self.model
        from apps.smallstack.api import _model_field_type

        if self.dfn.columns:
            resolved: list[tuple[str, str]] = []
            for col in self.dfn.columns:
                if isinstance(col, (tuple, list)):
                    resolved.append((col[0], col[1]))
                else:
                    resolved.append((col, _model_field_type(model, col)))
            return resolved
        return [
            (f.name, _model_field_type(model, f.name))
            for f in model._meta.fields
            if not f.primary_key
        ]

    def column_names(self) -> list[str]:
        return [name for name, _ in self.columns()]

    def filter_field_names(self) -> list[str]:
        if self.dfn.filters is not None:
            return list(self.dfn.filters)
        return self.column_names()

    # -- schema (may hit DB via _build_filter_meta's distinct-value probe) ----

    def schema(self) -> dict:
        """Typed columns (dimension vs measure) + filter widget metadata."""
        from apps.smallstack.crud import _build_filter_meta

        model = self.model
        cols = self.columns()
        columns = [
            {
                "name": name,
                "label": _label_for(model, name),
                "type": typ,
                "role": "measure" if typ in _MEASURE_TYPES else "dimension",
            }
            for name, typ in cols
        ]
        filter_names = set(self.filter_field_names())
        filters = []
        for name, _typ in cols:
            if name not in filter_names:
                continue
            meta = _build_filter_meta(model, name)
            if meta:
                filters.append(meta)
        return {
            "key": self.key,
            "label": self.dfn.label,
            "description": self.dfn.description,
            "columns": columns,
            "filters": filters,
        }

    # -- rows (sub-filtering reduces the row count) --------------------------

    def rows(
        self,
        *,
        filters: Optional[dict] = None,
        ordering: str = "",
        limit: int = 50,
        request=None,
    ) -> list[dict]:
        from apps.smallstack.api import serialize
        from apps.smallstack.crud import (
            _apply_list_filters as apply_filters,
        )
        from apps.smallstack.crud import (
            _apply_ordering_fields as apply_ordering,
        )

        qs = self._queryset(request)
        cfg = _ConfigAdapter(self)

        if filters:
            pairs = "&".join(
                f"{k}={v}" for k, v in filters.items() if v not in (None, "")
            )
            if pairs:
                filter_req = _fake_request(getattr(request, "user", None), pairs)
                qs = apply_filters(qs, filter_req, cfg)

        if ordering:
            qs = apply_ordering(qs, ordering, set(self.column_names()))

        try:
            limit = max(1, min(int(limit or 50), _ROWS_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 50

        field_names = self.column_names()
        return [serialize(obj, field_names, [], set()) for obj in qs[:limit]]

    # -- series (group-by rollup → [{label, value}] for bar/pie) -------------

    def series(
        self,
        dimension: str,
        *,
        measure: Optional[str] = None,
        agg: str = "count",
        limit: int = 50,
        request=None,
    ) -> list[dict]:
        from django.db.models import Avg, Count, Max, Min, Sum

        funcs = {"count": Count, "sum": Sum, "avg": Avg, "min": Min, "max": Max}
        if agg not in funcs:
            agg = "count"

        qs = self._queryset(request)
        if agg == "count" or not measure:
            annotated = qs.values(dimension).annotate(_value=Count("id"))
        else:
            annotated = qs.values(dimension).annotate(_value=funcs[agg](measure))
        annotated = annotated.order_by(dimension)

        try:
            limit = max(1, min(int(limit or 50), _SERIES_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 50

        out = []
        for row in annotated[:limit]:
            val = row["_value"]
            out.append(
                {
                    "label": _series_label(row.get(dimension)),
                    "value": round(val, 2) if isinstance(val, float) else val,
                }
            )
        return out


class _ConfigAdapter:
    """Presents the ``crud_config`` interface the pipeline helpers expect."""

    def __init__(self, ds: Dataset):
        self._ds = ds
        self.model = ds.model
        self.ordering_fields = ds.column_names()
        self.api_aggregate_fields = [
            name for name, typ in ds.columns() if typ in _MEASURE_TYPES
        ]

    def _resolve_filter_fields(self):
        return self._ds.filter_field_names()

    def _resolve_search_fields(self):
        return []

    def _get_list_fields(self):
        return self._ds.column_names()


# ---------------------------------------------------------------------------
# Public accessors (the builder / REST / MCP entry points)
# ---------------------------------------------------------------------------


def get_dataset(key: str) -> Optional[Dataset]:
    dfn = get_def(key)
    return Dataset(dfn) if dfn is not None else None


def list_datasets(*, api_only: bool = False) -> list[dict]:
    """The picker list — lightweight metadata only (no DB, no schema probe)."""
    return [
        {"key": d.key, "label": d.label, "description": d.description}
        for d in all_defs()
        if not api_only or d.enable_api
    ]
