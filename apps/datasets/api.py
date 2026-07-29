"""REST surface for datasets under ``/smallstack/datasets/``.

Opt-in per dataset (``enable_api = True``) and gated by the site-wide
``SMALLSTACK_DATASETS_ENABLED`` master switch. Staff-only by default (secure
default for an admin-area data surface); a downstream project that wants a
broader-audience surface builds its own view over ``core.get_dataset``.

Routes:
    GET  /smallstack/datasets/                  → list (api-exposed datasets)
    GET  /smallstack/datasets/<key>/schema/     → typed columns + filters
    GET  /smallstack/datasets/<key>/series/     → grouped [{label, value}]
    GET  /smallstack/datasets/<key>/            → filtered rows
"""

from __future__ import annotations

import csv

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.smallstack.api import _authenticate_api_request

from .core import Dataset, get_dataset, list_datasets
from .registry import get_def

_RESERVED = {"ordering", "limit", "offset", "format", "dimension", "measure", "agg", "expand"}


def _enabled() -> bool:
    return getattr(settings, "SMALLSTACK_DATASETS_ENABLED", True)


def _require_api_dataset(key: str) -> Dataset:
    dfn = get_def(key)
    if dfn is None or not dfn.enable_api:
        raise Http404("dataset not found")
    ds = get_dataset(key)
    assert ds is not None  # guaranteed by the get_def check above
    return ds


def _parse_expand(request: HttpRequest) -> list[str]:
    raw = request.GET.get("expand", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _csv_cell(val: object) -> object:
    """Flatten a row value for a CSV cell (expanded FK dict → its name)."""
    if isinstance(val, dict) and "name" in val:
        return val["name"]
    if isinstance(val, bool):
        return str(val).lower()
    return "" if val is None else val


def _rows_csv_response(ds: Dataset, key: str, rows: list[dict]) -> HttpResponse:
    """Stream ``rows`` as CSV: schema columns first, then any extra keys."""
    header = list(ds.column_names())
    for row in rows:
        for k in row:
            if k not in header:
                header.append(k)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{key}.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col)) for col in header])
    return response


@method_decorator(csrf_exempt, name="dispatch")
class _DatasetApiView(View):
    """Base view: Bearer-or-session auth returning JSON 401/403 (never a 302).

    Uses the same ``_authenticate_api_request`` path as ``/api/`` so a
    cross-origin SPA sending an ``Authorization: Bearer`` header can reach the
    datasets surface. Staff-only by default (secure default for an admin-area
    data surface), enforced as a JSON 403.
    """

    require_staff = True

    def dispatch(self, request, *args, **kwargs):
        if not _enabled():
            raise Http404
        user, err = _authenticate_api_request(request)
        if err:
            return err
        if self.require_staff and not user.is_staff:
            return JsonResponse({"error": "Staff access required"}, status=403)
        return super().dispatch(request, *args, **kwargs)


class DatasetListView(_DatasetApiView):
    def get(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse({"results": list_datasets(api_only=True)})


class DatasetSchemaView(_DatasetApiView):
    def get(self, request: HttpRequest, key: str) -> JsonResponse:
        ds = _require_api_dataset(key)
        return JsonResponse(ds.schema())


class DatasetRowsView(_DatasetApiView):
    def get(self, request: HttpRequest, key: str) -> JsonResponse:
        ds = _require_api_dataset(key)
        filters = {k: v for k, v in request.GET.items() if k not in _RESERVED}
        is_csv = request.GET.get("format") == "csv"
        # CSV is the whole filtered set (an export), ignoring paging unless the
        # caller explicitly passes limit/offset; JSON pages (default limit 50).
        if is_csv:
            limit = request.GET.get("limit")  # None (absent) → unbounded
            offset = request.GET.get("offset", 0)
        else:
            limit = request.GET.get("limit", 50)
            offset = request.GET.get("offset", 0)
        try:
            rows = ds.rows(
                filters=filters,
                ordering=request.GET.get("ordering", ""),
                limit=limit,
                offset=offset,
                expand=_parse_expand(request),
                request=request,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if is_csv:
            return _rows_csv_response(ds, key, rows)
        try:
            offset_echo = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset_echo = 0
        # ``total`` is the full matching count (for "N of TOTAL" paging);
        # ``count`` stays the length of this page (unchanged for compatibility).
        return JsonResponse(
            {
                "key": key,
                "count": len(rows),
                "total": ds.count(request=request, filters=filters),
                "offset": offset_echo,
                "results": rows,
            }
        )


class DatasetSeriesView(_DatasetApiView):
    def get(self, request: HttpRequest, key: str) -> JsonResponse:
        ds = _require_api_dataset(key)
        # A blank/omitted dimension collapses to a single ungrouped aggregate
        # (the KPI/scalar path) instead of erroring.
        dimension = request.GET.get("dimension", "").strip() or None
        # Non-reserved query params are filters — same contract as the rows view,
        # so the chart reflects the builder's active filters.
        filters = {k: v for k, v in request.GET.items() if k not in _RESERVED}
        try:
            series = ds.series(
                dimension,
                measure=(request.GET.get("measure") or None),
                agg=request.GET.get("agg", "count"),
                limit=request.GET.get("limit", 50),
                filters=filters,
                request=request,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({"key": key, "dimension": dimension, "series": series})


class DatasetScalarView(_DatasetApiView):
    """KPI tile: a single ungrouped aggregate → ``{value}`` (no GROUP BY)."""

    def get(self, request: HttpRequest, key: str) -> JsonResponse:
        ds = _require_api_dataset(key)
        agg = request.GET.get("agg", "count")
        measure = request.GET.get("measure") or None
        # Non-reserved query params are filters — same contract as the rows view,
        # so the KPI reflects the builder's active filters.
        filters = {k: v for k, v in request.GET.items() if k not in _RESERVED}
        try:
            value = ds.scalar(
                measure=measure, agg=agg, filters=filters, request=request
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(
            {"key": key, "agg": agg, "measure": measure, "value": value}
        )
