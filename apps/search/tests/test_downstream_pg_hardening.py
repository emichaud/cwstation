"""Regression tests for the Postgres/search hardening upstreamed from the
downstream call_stats post-mortem (ai_cowork/rag/postgres-search-hardening.md).

Engine-portable: the behaviors here (bulk-write reindex, identifier
normalization, URL fallback, SQLite field drift, diagnostics shape) are all
exercised on the SQLite dev/test engine.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.search.backends.base import IndexedView
from apps.search.backends.fallback import _detail_url_for, _make_hit
from apps.search.backends.sqlite_fts import SQLiteFTSBackend, _fts_columns, _fts_table
from apps.search.maintenance import digits_search, reindex_instances

pytestmark = pytest.mark.django_db


def _is_sqlite() -> bool:
    return connection.vendor == "sqlite"


# --- #11 digits_search — pure, engine-independent ---------------------------


def test_digits_search_emits_10_and_11_digit_forms():
    assert digits_search("+13128482994") == "13128482994 3128482994"


def test_digits_search_strips_formatting():
    assert digits_search("(312) 848-2994") == "3128482994"


def test_digits_search_handles_empty_and_multiple():
    assert digits_search("", None) == ""
    # dedupes overlapping forms, preserves order
    assert digits_search("13128482994", "3128482994") == "13128482994 3128482994"


# --- #4 reindex_instances — bulk writes bypass the index --------------------


@pytest.fixture
def user_view():
    return IndexedView(
        view_cls=type("DummyUserView", (), {}),
        model=get_user_model(),
        fields=["username", "email"],
        display_field="username",
        subtitle_field="email",
    )


@pytest.fixture
def registered_user_index(user_view, search_registry_snapshot):
    """Register the user view + create its FTS table; drop the table after."""
    if not _is_sqlite():
        pytest.skip("requires SQLite FTS backend")
    search_registry_snapshot[user_view.model_label] = user_view
    SQLiteFTSBackend().ensure_index(user_view)
    yield user_view
    with connection.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{_fts_table(user_view)}"')


def test_bulk_create_is_invisible_until_reindexed(registered_user_index):
    view = registered_user_index
    User = get_user_model()
    backend = SQLiteFTSBackend()

    # bulk_create fires no post_save signals → rows are NOT indexed.
    User.objects.bulk_create([
        User(username="bulkzaphod", email="z@example.com"),
        User(username="bulktrillian", email="t@example.com"),
    ])
    assert backend.query(view, "bulkzaphod") == []

    # The helper closes the gap.
    n = reindex_instances(User, User.objects.filter(username__startswith="bulk"))
    assert n == 2
    assert len(backend.query(view, "bulkzaphod")) == 1


def test_reindex_whole_model_with_no_arg(registered_user_index):
    view = registered_user_index
    User = get_user_model()
    User.objects.bulk_create([User(username="wholemodelfoo", email="f@example.com")])
    reindex_instances(User)  # no objects arg → every row
    assert len(SQLiteFTSBackend().query(view, "wholemodelfoo")) == 1


def test_reindex_unregistered_model_is_a_loud_noop(search_registry_snapshot):
    # Registry emptied by the snapshot → user model isn't a searchable view.
    assert reindex_instances(get_user_model()) == 0


# --- #9 clickable results: CRUDView detail-URL fallback ---------------------


def test_detail_url_none_without_url_base():
    view = IndexedView(view_cls=type("NoUrlBase", (), {}), model=get_user_model(), fields=["username"])
    assert _detail_url_for(view, get_user_model()(pk=1)) is None


def test_detail_url_none_on_unreversible_route():
    view = IndexedView(
        view_cls=type("BogusBase", (), {"url_base": "nonexistent/route"}),
        model=get_user_model(),
        fields=["username"],
    )
    assert _detail_url_for(view, get_user_model()(pk=1)) is None


def test_make_hit_prefers_get_absolute_url():
    class _Obj:
        pk = 7

        def get_absolute_url(self):
            return "/explicit/url/"

        def __str__(self):
            return "obj7"

    view = IndexedView(
        view_cls=type("V", (), {"url_base": "whatever"}),
        model=get_user_model(),  # only used for model_label/verbose metadata
        fields=["name"],
    )
    hit = _make_hit(view, _Obj())
    assert hit.url == "/explicit/url/"


# --- #10 SQLite search_fields drift recreates the FTS table -----------------


def test_sqlite_fts_recreated_on_field_change():
    if not _is_sqlite():
        pytest.skip("requires SQLite FTS backend")
    backend = SQLiteFTSBackend()
    User = get_user_model()

    v1 = IndexedView(view_cls=type("V1", (), {}), model=User, fields=["username", "email"])
    backend.ensure_index(v1)
    table = _fts_table(v1)
    with connection.cursor() as cur:
        assert _fts_columns(cur, table) == ["object_id", "username", "email"]

    # Same model/table, new field set — must be detected and recreated.
    v2 = IndexedView(view_cls=type("V2", (), {}), model=User, fields=["username", "email", "first_name"])
    backend.ensure_index(v2)
    with connection.cursor() as cur:
        assert _fts_columns(cur, table) == ["object_id", "username", "email", "first_name"]
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')


# --- diagnostics: structured data + text report -----------------------------


def test_collect_diagnostics_shape_without_query():
    from apps.search.diagnostics import collect_diagnostics, format_diagnostics_text

    data = collect_diagnostics()
    assert data["vendor"] == connection.vendor
    assert "backend" in data
    assert isinstance(data["sources"], list)
    assert data["timings"] == {}  # no query → no timing
    assert "SmallStack search diagnostics" in format_diagnostics_text(data)


def test_collect_diagnostics_times_when_given_a_query():
    from apps.search.diagnostics import collect_diagnostics

    data = collect_diagnostics("admin")
    assert "search_all_ms" in data["timings"]
    assert "per_source" in data["timings"]
