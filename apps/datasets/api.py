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

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views import View

from apps.smallstack.mixins import StaffRequiredMixin

from .core import get_dataset, list_datasets
from .registry import get_def

_RESERVED = {"ordering", "limit", "format", "dimension", "measure", "agg"}


def _enabled() -> bool:
    return getattr(settings, "SMALLSTACK_DATASETS_ENABLED", True)


def _require_api_dataset(key: str):
    dfn = get_def(key)
    if dfn is None or not dfn.enable_api:
        raise Http404("dataset not found")
    return get_dataset(key)


class DatasetListView(StaffRequiredMixin, View):
    def get(self, request):
        if not _enabled():
            raise Http404
        return JsonResponse({"results": list_datasets(api_only=True)})


class DatasetSchemaView(StaffRequiredMixin, View):
    def get(self, request, key):
        if not _enabled():
            raise Http404
        ds = _require_api_dataset(key)
        return JsonResponse(ds.schema())


class DatasetRowsView(StaffRequiredMixin, View):
    def get(self, request, key):
        if not _enabled():
            raise Http404
        ds = _require_api_dataset(key)
        filters = {k: v for k, v in request.GET.items() if k not in _RESERVED}
        rows = ds.rows(
            filters=filters,
            ordering=request.GET.get("ordering", ""),
            limit=request.GET.get("limit", 50),
            request=request,
        )
        return JsonResponse({"key": key, "count": len(rows), "results": rows})


class DatasetSeriesView(StaffRequiredMixin, View):
    def get(self, request, key):
        if not _enabled():
            raise Http404
        ds = _require_api_dataset(key)
        dimension = request.GET.get("dimension", "").strip()
        if not dimension:
            return JsonResponse({"error": "dimension query param is required"}, status=400)
        series = ds.series(
            dimension,
            measure=(request.GET.get("measure") or None),
            agg=request.GET.get("agg", "count"),
            limit=request.GET.get("limit", 50),
            request=request,
        )
        return JsonResponse({"key": key, "dimension": dimension, "series": series})
