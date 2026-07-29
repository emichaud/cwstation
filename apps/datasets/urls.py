"""URL routes for the datasets REST surface (mounted at /smallstack/datasets/)."""

from __future__ import annotations

from django.urls import path

from .api import (
    DatasetListView,
    DatasetRowsView,
    DatasetSchemaView,
    DatasetSeriesView,
)

app_name = "datasets"

urlpatterns = [
    path("", DatasetListView.as_view(), name="list"),
    # Specific sub-routes before the catch-all <key>/ rows route.
    path("<str:key>/schema/", DatasetSchemaView.as_view(), name="schema"),
    path("<str:key>/series/", DatasetSeriesView.as_view(), name="series"),
    path("<str:key>/", DatasetRowsView.as_view(), name="rows"),
]
