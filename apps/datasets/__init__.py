"""Datasets — named, filterable querysets exposed as typed rows and columns.

A *dataset* is a function returning a Django ``QuerySet``, registered with the
``@dataset`` decorator. It is the abstract input a visual dashboard builder (or
an agent, over MCP/REST) consumes: pick from a list, read typed columns
(dimension vs measure), apply sub-filters that reduce the row count, and hand
the rows to a UI component. The base ships only the *seam* — the registry and
the schema/rows/series contract, all composed from the existing CRUDView list
pipeline. Chart rendering and saved dashboards live downstream.
"""

from __future__ import annotations

from .registry import dataset  # noqa: F401  (public decorator)

__all__ = ["dataset"]
