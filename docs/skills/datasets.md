# Datasets — filtered querysets as typed rows & columns (`@dataset`)

**Read this** before building anything that needs "a table of rows the user can slice and chart" — a dashboard builder, a report picker, an export screen, or an MCP/agent that queries structured data. A **dataset** is a named function returning a `QuerySet`; the framework turns it into typed rows + columns you can inspect, filter, and roll up **without knowing the underlying model**. That introspection is the whole point: you build the UI against the *schema*, not against a specific table.

> **Prerequisites**: [`modern-dark-theme.md`](modern-dark-theme.md) for the page/component patterns, [`building-a-user-facing-site.md`](building-a-user-facing-site.md) if the surface is non-staff.

## The one-paragraph model

Declare a dataset with `@dataset` over a queryset. The base derives everything a UI needs from `qs.model`: **column names, datatypes, a dimension/measure role, and ready-made filter widgets**. A visual builder then: lists datasets → reads one dataset's schema → offers field/filter pickers → asks for filtered `rows` or a grouped `series`. The builder is generic — it never hard-codes a model. Base ships **zero** datasets; you (or a downstream project) declare them.

## Declare a dataset

```python
from apps.datasets import dataset

@dataset("open_tickets", label="Open Tickets",
         description="Tickets still awaiting resolution",
         enable_api=True, enable_mcp=True)   # both default False
def open_tickets(request=None):
    return Ticket.objects.filter(status="open").select_related("assignee")
```

- The function takes **zero args or one `request`** (for per-user scoping). It returns a queryset — no model class, no CRUDView, no migration.
- Put it in any app's `datasets.py` — it's autodiscovered at startup and self-registers (zero wiring), exactly like a CRUDView in `views.py`.
- Flags are opt-in: `enable_api` publishes REST routes, `enable_mcp` publishes an agent tool. Neither is required — a dataset is useful in-process with neither flag set.

Key + flags reference:

| Arg | Meaning |
|---|---|
| `key` (required) | Stable id used in URLs / tool names (`query_dataset_<key>`) |
| `label`, `description` | Human text shown in the picker + tool description |
| `enable_api` | Publish `GET /smallstack/datasets/<key>/…` (staff-gated) |
| `enable_mcp` | Publish a `query_dataset_<key>` MCP tool; `mcp_access` sets the tier (default `"staff"`) |
| `filters` | Restrict which columns are filterable (default: all) |
| `columns` | Override the column list — needed only for annotations (see [Computed columns](#computed-columns)) |

## The four operations a UI is built from

```python
from apps.datasets.core import list_datasets, get_dataset

list_datasets()                       # picker list: [{key, label, description}]
ds = get_dataset("open_tickets")
ds.schema()                           # typed columns + filter widgets  ← build the UI from this
ds.rows(filters={"status": "open"})   # reduced rows: [ {col: val, ...}, ... ]
ds.series("assignee", measure="hours_spent", agg="sum")   # [{label, value}] for bar/pie
```

Over HTTP the same four are:

```
GET /smallstack/datasets/                       → list
GET /smallstack/datasets/<key>/schema/          → schema
GET /smallstack/datasets/<key>/?<field>=<val>   → rows (query params are filters)
GET /smallstack/datasets/<key>/series/?dimension=assignee&measure=hours_spent&agg=sum
```

## Inspecting a dataset to build an interface — the tricks

This is the section that matters for a builder. **`schema()` is your entire source of truth** — pull names, types, roles, and widgets from it and the UI writes itself.

### `schema()` shape

```jsonc
{
  "key": "open_tickets",
  "label": "Open Tickets",
  "columns": [
    {"name": "status",       "label": "Status",   "type": "string",   "role": "dimension"},
    {"name": "created_at",   "label": "Created",   "type": "datetime", "role": "dimension"},
    {"name": "assignee",     "label": "Assignee",  "type": "fk",       "role": "dimension"},
    {"name": "hours_spent",  "label": "Hours",     "type": "float",    "role": "measure"}
  ],
  "filters": [
    {"name": "status",   "label": "Status",  "type": "choice",  "choices": [["","All"],["open","Open"],["blocked","Blocked"]]},
    {"name": "created_at","label": "Created", "type": "choice",  "is_date": true, "choices": [["","All"],["today","Today"],["week","Past 7 days"], "..."]},
    {"name": "assignee", "label": "Assignee","type": "choice",  "choices": [["","All"],["3","Ada"],["7","Grace"], "..."]}
  ]
}
```

### Trick 1 — split pickers by `role`

`role` is the chart-affordance hint. Filter your field pickers by it and each chart type's inputs fall out automatically:

```python
sch = get_dataset(key).schema()
dimensions = [c for c in sch["columns"] if c["role"] == "dimension"]   # categories / axes / group-by
measures   = [c for c in sch["columns"] if c["role"] == "measure"]     # values to aggregate
```

- **Bar / line**: one `dimension` (X) + one `measure` (Y).
- **Pie / donut**: one `dimension` (slices); measure optional (defaults to row count).
- **KPI tile**: one `measure` + an `agg`.
- **Table**: any columns.

Disable a chart type in the UI when the dataset lacks the roles it needs (e.g. grey out bar charts if `measures` is empty) — you know that *before* fetching a single row.

### Trick 2 — build filter widgets straight from `filters`

Don't infer widgets. `schema()["filters"]` already tells you the control to render:

| `type` | Render as | Notes |
|---|---|---|
| `choice` (with `choices`) | dropdown / segmented control | Auto-detected from FK targets, `choices=`, or ≤30 distinct values. `choices` is `[[value, label], …]`; the first is usually `["", "All"]` |
| `choice` + `is_date: true` | date-range preset dropdown | Values are `today` / `yesterday` / `week` / `month` / `year` |
| `boolean` | toggle (All / Yes / No) | |
| `text` | free-text input | Matched with `icontains` server-side |

Each filter's `name` is the query key you send back to `rows()` — round-trips exactly.

### Trick 3 — map `type` to formatting

`type` is the raw Django-derived datatype; use it to format cells and pick input controls:

`string` · `integer` · `float` · `decimal` · `date` · `datetime` · `time` · `boolean` · `fk`

e.g. right-align + thousands-separate `integer`/`float`/`decimal`; render `datetime` with the `localtime_tooltip` tag; render `fk` values (which come back as `{"id", "name"}` when expanded, else the pk) as a link.

### Trick 4 — the round-trip is stable

Every `name` from `schema()` is the same key you pass back:

```python
ds.rows(filters={"status": "open", "created_at": "week"}, ordering="-created_at", limit=100)
ds.series("assignee", measure="hours_spent", agg="sum")
```

So a builder can be fully data-driven: read schema → collect user choices keyed by `name` → post them straight back. No translation layer.

### Trick 5 — probe cheaply, fetch lazily

- `list_datasets()` is **metadata only** (no DB, no schema probe) — safe to call on every page load for the picker.
- `schema()` runs a **small** query per categorical column (distinct-value probe) — call it once when a dataset is selected, then cache it for the session.
- `rows()` / `series()` are the only heavy calls — trigger them on an explicit "run / preview," not on every keystroke.

## Computed columns

`type`/`role` are inferred from **real model fields**. A column produced by `.annotate()` isn't a model field, so declare it explicitly — then it flows through `schema()` with the right type and role:

```python
from django.db.models import Count

@dataset("tickets_by_assignee", columns=[("assignee", "fk"), ("ticket_count", "integer")])
def tickets_by_assignee(request=None):
    return (Ticket.objects.values("assignee")
            .annotate(ticket_count=Count("id")))
```

Model-field columns need nothing; only computed ones need the `(name, type)` hint.

## Exposing to agents (MCP)

Set `enable_mcp=True` and the dataset becomes a `query_dataset_<key>` tool: the agent passes filter args, gets rows — or passes `group_by` (+ optional `measure`/`agg`) and gets a `[{label, value}]` series. A site-level `list_datasets` tool lets the agent discover what's queryable. Secure default is `mcp_access="staff"`; widen deliberately. This is the same "declare once, surface everywhere" pattern as `enable_api`/`enable_mcp` on a CRUDView.

## When to reach for a dataset vs. a CRUDView

- **Dataset** — read-only, analytical: "a filtered table to slice, chart, or export." No edit/detail pages. Can join/annotate/aggregate; source is any queryset.
- **CRUDView** — the record is a first-class editable thing (admin + REST + detail pages). If you need create/update/delete UI, use a CRUDView; if you only need to *read and shape* rows, use a dataset.

They compose: a dataset can return a queryset over the same model a CRUDView manages.

## Settings & gotchas

- Master switch: `SMALLSTACK_DATASETS_ENABLED` (default on). Off → REST 404s and no MCP tools register; per-dataset `enable_*` become no-ops.
- REST is **staff-only** by default (secure default for an admin data surface). For a non-staff end-user builder, build your own view over `get_dataset(key)` with the right auth + tenancy scoping — see [`building-a-user-facing-site.md`](building-a-user-facing-site.md). Scope per-user data inside the dataset function via its `request` arg.
- `filters` defaults to **all columns**. Numeric columns filter by exact match; text columns with many distinct values fall back to substring (`icontains`); low-cardinality columns become dropdowns.
- Base ships **no datasets** — the app is the seam only. Declare yours in an app's `datasets.py`.

## Anti-patterns

1. **Hard-coding column lists in the builder** — read `schema()["columns"]` instead; the whole design is that the UI adapts to any dataset.
2. **Inferring filter widgets from `type`** — use `schema()["filters"]`, which already resolved FK/choice/date/boolean widgets (and ran the categorical detection for you).
3. **Calling `schema()` on every render** — cache it per selected dataset; it does per-column distinct probes.
4. **Fetching rows to compute a chart total** — use `series(dimension, measure, agg)`; it rolls up in the database, not in Python.
5. **Forgetting `columns=` for annotations** — an annotated column with no hint types as `string`/`dimension` and won't offer as a measure.
