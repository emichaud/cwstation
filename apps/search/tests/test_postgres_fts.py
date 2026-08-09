"""PostgresFTSBackend — tsvector + GIN, set-based & per-row rebuild, ranked query.

Runs only on Postgres (`TEST_DB=postgres`); skips on SQLite. Closes the review's
F4 gap (postgres_fts.py was at 0% because the default suite is SQLite-only).
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from apps.search.backends.base import IndexedView
from apps.search.backends.postgres_fts import (
    PostgresFTSBackend,
    _gin_index_name,
    _index_exists,
    _set_based_columns,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _require_pg():
    if connection.vendor != "postgresql":
        pytest.skip("PostgresFTSBackend requires Postgres (run with TEST_DB=postgres)")


@pytest.fixture
def view():
    return IndexedView(
        view_cls=type("DummyView", (), {}),
        model=get_user_model(),
        fields=["username", "email"],
        display_field="username",
        subtitle_field="email",
    )


@pytest.fixture
def backend(view):
    bk = PostgresFTSBackend()
    bk.ensure_index(view)
    return bk


def test_ensure_index_adds_column_and_gin(backend, view):
    table = view.model._meta.db_table
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=%s AND column_name='search_vector'",
            [table],
        )
        assert cur.fetchone() is not None
    assert _index_exists(_gin_index_name(view)) is True
    # Idempotent.
    assert backend.ensure_index(view) is True


def test_index_and_query_roundtrip(backend, view):
    User = get_user_model()
    user = User.objects.create_user(username="pgalpha", email="alpha@example.com")
    backend.index_object(view, user)
    hits = backend.query(view, "pgalpha")
    assert len(hits) == 1
    assert hits[0].object_id == user.pk
    assert hits[0].display == "pgalpha"


def test_query_ranks_and_limits(backend, view):
    User = get_user_model()
    # All three stem to "manag" under the english config, so one query matches
    # all three; limit caps the result.
    for name in ("manage", "manager", "managing"):
        backend.index_object(view, User.objects.create_user(username=name, email=f"{name}@e.com"))
    hits = backend.query(view, "managing", limit=2)
    assert len(hits) == 2
    assert all(h.rank >= 0 for h in hits)


def test_query_empty_returns_empty(backend, view):
    assert backend.query(view, "") == []
    assert backend.query(view, "   ") == []


def test_set_based_rebuild_populates_vectors(backend, view):
    User = get_user_model()
    # bulk_create bypasses signals → no vectors until rebuild.
    User.objects.bulk_create([
        User(username="bulkone", email="b1@example.com"),
        User(username="bulktwo", email="b2@example.com"),
    ])
    assert backend.query(view, "bulkone") == []
    count = backend.rebuild(view)
    assert count >= 2
    with connection.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM "{view.model._meta.db_table}" WHERE search_vector IS NULL'
        )
        assert cur.fetchone()[0] == 0
    assert len(backend.query(view, "bulkone")) == 1


def test_per_row_rebuild_for_property_field():
    # `is_authenticated` is a property, not a column → forces the per-row path.
    view = IndexedView(
        view_cls=type("V", (), {}),
        model=get_user_model(),
        fields=["username", "is_authenticated"],
        display_field="username",
    )
    backend = PostgresFTSBackend()
    backend.ensure_index(view)
    assert _set_based_columns(view) is None  # property → not set-based
    get_user_model().objects.bulk_create([get_user_model()(username="proprow", email="p@e.com")])
    assert backend.rebuild(view) >= 1
    assert any(h.display == "proprow" for h in backend.query(view, "proprow"))


def test_set_based_columns_detection(view):
    cols = _set_based_columns(view)  # username + email, both local columns
    assert cols is not None
    assert {c[0] for c in cols} == {"username", "email"}
    # A __ path is not a local column.
    v2 = IndexedView(view_cls=type("V", (), {}), model=get_user_model(), fields=["username__x"])
    assert _set_based_columns(v2) is None


def test_remove_object_is_noop_row_delete_drops_vector(backend, view):
    User = get_user_model()
    user = User.objects.create_user(username="pggamma", email="g@example.com")
    backend.index_object(view, user)
    assert len(backend.query(view, "pggamma")) == 1
    backend.remove_object(view, user.pk)  # no-op — vector lives on the row
    user.delete()
    assert backend.query(view, "pggamma") == []
