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

from django.db.models import QuerySet
from django.http import HttpRequest, QueryDict

from .registry import DatasetDef, all_defs, get_def

# Types the framework classifies as numeric → chartable "measures".
_MEASURE_TYPES = {"integer", "float", "decimal"}
_SERIES_LIMIT_MAX = 500
_ROWS_LIMIT_MAX = 500
_AGG_NAMES = ("count", "sum", "avg", "min", "max")


def _agg_func(agg: str, measure: Optional[str]):
    """Return the aggregate expression for ``agg`` over ``measure``.

    ``count`` (or a missing measure) counts rows; the others aggregate the
    measure column. ``agg`` is assumed validated by the caller.
    """
    from django.db.models import Avg, Count, Max, Min, Sum

    if agg == "count" or not measure:
        return Count("id")
    return {"sum": Sum, "avg": Avg, "min": Min, "max": Max}[agg](measure)


def _round_value(val: Any) -> Any:
    return round(val, 2) if isinstance(val, float) else val


def _fake_request(user: Any = None, query: str = "") -> HttpRequest:
    """Minimal request so the pipeline helpers can read ``.user`` / ``.GET``."""
    req = HttpRequest()
    req.method = "GET"
    req.user = user
    req.GET = QueryDict(query, mutable=False)
    req.META = {}
    return req


def _label_for(model: type, name: str) -> str:
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

    def __init__(self, dfn: DatasetDef) -> None:
        self.dfn = dfn
        self.key = dfn.key

    # -- queryset / model ----------------------------------------------------

    def _queryset(self, request: HttpRequest | None = None) -> QuerySet:
        """Build the source queryset (lazy — no DB hit)."""
        fn = self.dfn.fn
        try:
            takes_arg = len(inspect.signature(fn).parameters) >= 1
        except (TypeError, ValueError):
            takes_arg = False
        return fn(request) if takes_arg else fn()

    @property
    def model(self) -> type:
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

    def fk_column_names(self) -> set[str]:
        """Names of columns the schema classifies as foreign keys."""
        return {name for name, typ in self.columns() if typ == "fk"}

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

    # -- shared filter application (one contract for rows/series/scalar) ------

    def _filtered_qs(
        self,
        request: HttpRequest | None,
        filters: Optional[dict],
    ) -> QuerySet:
        """Source queryset with *filters* applied via the CRUD list pipeline.

        The single filter-application path shared by ``rows``, ``series`` and
        ``scalar`` — so all three read paths honour the same filter contract
        (an active builder filter reduces the table, the chart *and* the KPI).
        Unknown filter keys are tolerated (ignored) exactly as the list view
        tolerates them.
        """
        from apps.smallstack.crud import _apply_list_filters as apply_filters

        qs = self._queryset(request)
        if filters:
            pairs = "&".join(
                f"{k}={v}" for k, v in filters.items() if v not in (None, "")
            )
            if pairs:
                filter_req = _fake_request(getattr(request, "user", None), pairs)
                qs = apply_filters(qs, filter_req, _ConfigAdapter(self))
        return qs

    # -- rows (sub-filtering reduces the row count) --------------------------

    def rows(
        self,
        *,
        filters: Optional[dict] = None,
        ordering: str = "",
        limit: int = 50,
        expand: Optional[list[str]] = None,
        request: HttpRequest | None = None,
    ) -> list[dict]:
        from apps.smallstack.api import serialize
        from apps.smallstack.crud import (
            _apply_ordering_fields as apply_ordering,
        )

        qs = self._filtered_qs(request, filters)

        if ordering:
            qs = apply_ordering(qs, ordering, set(self.column_names()))

        try:
            limit = max(1, min(int(limit or 50), _ROWS_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 50

        field_names = self.column_names()

        # A computed/annotated dataset (``.values(...).annotate(...)``) yields
        # plain dicts, not model instances — they have no ``.pk`` for
        # ``serialize`` and are already the shape a table wants. Return them
        # projected onto the declared columns (plus any annotations they carry).
        if getattr(qs.query, "values_select", None):
            return [self._project_dict_row(row, field_names) for row in qs[:limit]]

        # Only expand FK columns the schema actually knows about, so a bad
        # ``expand`` param can never raise inside ``serialize``.
        expand_fields = self.fk_column_names() & set(expand or [])
        return [
            serialize(obj, field_names, [], expand_fields) for obj in qs[:limit]
        ]

    @staticmethod
    def _project_dict_row(row: dict, field_names: list[str]) -> dict:
        """Shape a values-queryset dict into a stable row.

        Declared columns come first (missing keys → ``None``); any extra
        annotation keys the queryset carries are appended so callers still see
        aggregates like ``item_count`` even when they weren't declared columns.
        """
        out = {name: row.get(name) for name in field_names}
        for key, val in row.items():
            if key not in out:
                out[key] = val
        return out

    # -- series (group-by rollup → [{label, value}] for bar/pie) -------------

    def series(
        self,
        dimension: Optional[str] = None,
        *,
        measure: Optional[str] = None,
        agg: str = "count",
        limit: int = 50,
        filters: Optional[dict] = None,
        request: HttpRequest | None = None,
    ) -> list[dict]:
        """Grouped rollup → ``[{label, value}]`` for bar/pie.

        ``filters`` are applied through the same path as :meth:`rows` (one
        filter contract across all three read paths) so the chart reflects the
        builder's active filters.

        With ``dimension=None`` this collapses to a single **ungrouped**
        aggregate (the KPI/scalar path): one DB round-trip, one row whose label
        is the agg name — see :meth:`scalar` for the bare-number convenience.
        """
        if agg not in _AGG_NAMES:
            agg = "count"

        # Validate dimension/measure against the declared columns *before*
        # touching the ORM, so a bad param yields a clean ValueError (mapped to
        # HTTP 400 at the view boundary) rather than a raw FieldError / 500.
        # Mirrors how ``rows()`` already tolerates unknown ordering/filter keys.
        valid = set(self.column_names())
        if dimension is not None and dimension not in valid:
            raise ValueError(
                f"unknown dimension {dimension!r}; valid: {sorted(valid)}"
            )
        if measure and measure not in valid:
            raise ValueError(
                f"unknown measure {measure!r}; valid: {sorted(valid)}"
            )

        qs = self._filtered_qs(request, filters)

        # KPI / scalar path — no GROUP BY, single aggregate over the filtered set.
        if dimension is None:
            value = qs.aggregate(_value=_agg_func(agg, measure))["_value"] or 0
            return [{"label": agg, "value": _round_value(value)}]

        annotated = qs.values(dimension).annotate(_value=_agg_func(agg, measure))
        annotated = annotated.order_by(dimension)

        try:
            limit = max(1, min(int(limit or 50), _SERIES_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 50

        rows = list(annotated[:limit])

        # When the dimension is an FK, its group-by values are bare pks — resolve
        # them to the related object's ``str()`` so charts read "Electrical",
        # not "2". Single extra query (pk→name map), not N.
        label_map = self._fk_label_map(dimension, rows)

        out = []
        for row in rows:
            raw = row.get(dimension)
            label = label_map.get(raw) if label_map else None
            out.append(
                {
                    "label": label if label is not None else _series_label(raw),
                    "value": _round_value(row["_value"]),
                }
            )
        return out

    # -- scalar (ungrouped aggregate → a single number for a KPI tile) --------

    def scalar(
        self,
        *,
        measure: Optional[str] = None,
        agg: str = "count",
        filters: Optional[dict] = None,
        request: HttpRequest | None = None,
    ) -> Any:
        """One number for a KPI tile — an ungrouped ``qs.aggregate()``.

        ``agg="count"`` (or no ``measure``) counts rows; otherwise the measure
        column is aggregated. ``filters`` are applied through the same path as
        :meth:`rows`, so the KPI reflects the builder's active filters. Rolls up
        in the database, not in Python — the first-class path the "KPI tile"
        affordance needs (no GROUP BY).
        """
        return self.series(
            None, measure=measure, agg=agg, filters=filters, request=request
        )[0]["value"]

    def _fk_label_map(self, dimension: str, rows: list[dict]) -> dict:
        """Map FK pks in *rows* to the related object's ``str()``. ``{}`` if not an FK."""
        if dimension not in self.fk_column_names():
            return {}
        try:
            field = self.model._meta.get_field(dimension)
            related = field.related_model
        except Exception:
            return {}
        pks = [r.get(dimension) for r in rows if r.get(dimension) is not None]
        if not pks:
            return {}
        return {obj.pk: str(obj) for obj in related.objects.filter(pk__in=pks)}


class _ConfigAdapter:
    """Presents the ``crud_config`` interface the pipeline helpers expect."""

    def __init__(self, ds: Dataset) -> None:
        self._ds = ds
        self.model = ds.model
        self.ordering_fields = ds.column_names()
        self.api_aggregate_fields = [
            name for name, typ in ds.columns() if typ in _MEASURE_TYPES
        ]

    def _resolve_filter_fields(self) -> list[str]:
        return self._ds.filter_field_names()

    def _resolve_search_fields(self) -> list[str]:
        return []

    def _get_list_fields(self) -> list[str]:
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
