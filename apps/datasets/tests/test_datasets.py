"""Tests for the @dataset primitive: registry, schema roles, rows, series, MCP.

Uses apps.activity.RequestLog as the source model — it has numeric measures
(response_time_ms, status_code) and categorical dimensions (method, path),
which exercises the dimension/measure classification cleanly.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import async_to_sync

from apps.activity.models import RequestLog
from apps.datasets.core import get_dataset, list_datasets
from apps.datasets.registry import dataset

pytestmark = pytest.mark.django_db


# A test dataset registered at import time (unique key, harmless if it lingers).
@dataset(
    "t_requestlog",
    label="Test Request Log",
    description="Request rows for dataset tests.",
    enable_api=True,
    enable_mcp=True,
    mcp_access="readonly",
)
def _t_requestlog(request=None):
    return RequestLog.objects.all()


def _seed():
    """3 GET rows + 1 POST row."""
    for i in range(3):
        RequestLog.objects.create(
            path=f"/a/{i}", method="GET", status_code=200, response_time_ms=10 + i
        )
    RequestLog.objects.create(
        path="/b", method="POST", status_code=201, response_time_ms=40
    )


# --- registry + schema ------------------------------------------------------


def test_dataset_is_registered_and_listed():
    keys = {d["key"] for d in list_datasets()}
    assert "t_requestlog" in keys
    api_keys = {d["key"] for d in list_datasets(api_only=True)}
    assert "t_requestlog" in api_keys


def test_schema_classifies_dimension_and_measure():
    schema = get_dataset("t_requestlog").schema()
    roles = {c["name"]: c["role"] for c in schema["columns"]}
    # Numeric fields → measures; text/date/fk → dimensions.
    assert roles["response_time_ms"] == "measure"
    assert roles["status_code"] == "measure"
    assert roles["method"] == "dimension"
    assert roles["timestamp"] == "dimension"
    # Filters carry widget metadata for the builder.
    assert any(f["name"] == "method" for f in schema["filters"])


# --- rows (sub-filtering reduces the row count) -----------------------------


def test_rows_returns_typed_dicts():
    _seed()
    rows = get_dataset("t_requestlog").rows(limit=100)
    assert len(rows) == 4
    assert {"id", "method", "response_time_ms"} <= set(rows[0].keys())


def test_rows_subfilter_reduces_count():
    _seed()
    ds = get_dataset("t_requestlog")
    all_rows = ds.rows(limit=100)
    filtered = ds.rows(filters={"method": "GET"}, limit=100)
    assert len(all_rows) == 4
    assert len(filtered) == 3
    assert all(r["method"] == "GET" for r in filtered)


def test_rows_ordering_applies():
    _seed()
    rows = get_dataset("t_requestlog").rows(ordering="-response_time_ms", limit=100)
    times = [r["response_time_ms"] for r in rows]
    assert times == sorted(times, reverse=True)


# --- series (group-by rollup → [{label, value}]) ----------------------------


def test_series_counts_by_dimension():
    _seed()
    series = get_dataset("t_requestlog").series("method")
    as_dict = {s["label"]: s["value"] for s in series}
    assert as_dict == {"GET": 3, "POST": 1}


def test_series_sum_measure():
    _seed()
    series = get_dataset("t_requestlog").series("method", measure="response_time_ms", agg="sum")
    as_dict = {s["label"]: s["value"] for s in series}
    # GET rows: 10+11+12 = 33; POST: 40
    assert as_dict == {"GET": 33, "POST": 40}


# --- MCP tools --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_dataset_tools_registered():
    """Re-register dataset MCP tools each test (the MCP suite wipes the registry)."""
    from apps.datasets.mcp_tools import register_dataset_tools

    register_dataset_tools()


def _call_handler(name, args, *, user=None):
    from apps.mcp.server import TOOL_HANDLERS, ToolContext, reset_context, set_context

    token = set_context(ToolContext(user=user, token=None))
    try:
        handler = TOOL_HANDLERS[name]
        if asyncio.iscoroutinefunction(handler):
            return async_to_sync(lambda: handler(args))()
        return handler(args)
    finally:
        reset_context(token)


def test_mcp_tools_registered():
    from apps.mcp.server import TOOL_REGISTRY

    assert "list_datasets" in TOOL_REGISTRY
    assert "query_dataset_t_requestlog" in TOOL_REGISTRY
    td = TOOL_REGISTRY["query_dataset_t_requestlog"]
    assert td.requires_access == "readonly"
    # Filter fields surface as input properties.
    assert "method" in td.input_schema["properties"]
    assert "group_by" in td.input_schema["properties"]


def test_list_datasets_tool_returns_key():
    out = _call_handler("list_datasets", {})
    keys = {d["key"] for d in out["results"]}
    assert "t_requestlog" in keys


def test_query_dataset_tool_returns_rows():
    _seed()
    out = _call_handler("query_dataset_t_requestlog", {"method": "GET", "limit": 100})
    assert out["mode"] == "rows"
    assert out["count"] == 3
    assert all(r["method"] == "GET" for r in out["results"])


def test_query_dataset_tool_series_mode():
    _seed()
    out = _call_handler("query_dataset_t_requestlog", {"group_by": "method"})
    assert out["mode"] == "series"
    as_dict = {s["label"]: s["value"] for s in out["series"]}
    assert as_dict == {"GET": 3, "POST": 1}
