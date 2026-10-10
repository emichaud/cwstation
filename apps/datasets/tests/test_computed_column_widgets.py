"""`schema()["filters"]` must advertise widgets for COMPUTED columns too.

v0.21.11 made filters on computed (annotated) columns actually apply, but
`_build_filter_meta` needs a MODEL field, so a computed column declared
`filterable=` still produced no widget — a builder UI couldn't offer the
control even though the filter worked when passed. This closes that half.

The design constraint worth keeping in mind: in this framework a **choice
widget means an exact match** and a **text widget means `icontains`**
(`_apply_list_filters` only uses `icontains` when `meta["type"] == "text"`).
Computed columns always compare exactly, so a dropdown is the only widget
whose semantics we can honour — which is why a high-cardinality computed
string is advertised as *nothing* rather than as a text box that would promise
substring matching and not deliver it. The filter still applies either way.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    DateTimeField,
    F,
    Value,
    When,
)
from django.db.models.functions import Concat

from apps.datasets import core
from apps.datasets.registry import dataset

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def widget_ds(db):
    @dataset(
        "t_computed_widgets",
        label="Computed widgets probe",
        columns=[
            ("username", "str"),
            ("answered", "bool"),
            ("hours_band", "str"),
            ("seen_at", "datetime"),
            ("login_count", "int"),
            ("handle", "str"),
        ],
        filterable=[
            "username",
            "answered",
            "hours_band",
            "seen_at",
            "login_count",
            "handle",
        ],
    )
    def _ds():
        return User.objects.annotate(
            answered=Case(
                When(is_staff=True, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            hours_band=Case(
                When(is_staff=True, then=Value("business")),
                default=Value("after_hours"),
                output_field=CharField(),
            ),
            seen_at=Case(
                When(is_staff=True, then=F("date_joined")),
                default=F("date_joined"),
                output_field=DateTimeField(),
            ),
            login_count=Count("logentry"),
            handle=Concat(Value("@"), F("username"), output_field=CharField()),
        )

    return core.get_dataset("t_computed_widgets")


def _filters(ds) -> dict[str, dict]:
    return {f["name"]: f for f in ds.schema()["filters"]}


@pytest.fixture
def people(db):
    return [
        User.objects.create_user("staff_a", password="p", is_staff=True),
        User.objects.create_user("staff_b", password="p", is_staff=True),
        User.objects.create_user("plain_c", password="p", is_staff=False),
    ]


# --- the widgets we can honour ----------------------------------------------


def test_boolean_computed_column_gets_a_boolean_widget(widget_ds, people):
    meta = _filters(widget_ds)["answered"]
    assert meta["type"] == "boolean"
    assert meta["choices"] == [("", "All"), ("true", "Yes"), ("false", "No")]


def test_categorical_computed_column_gets_its_real_values(widget_ds, people):
    """The reported shape: `hours_band` is offered as a dropdown of the
    values actually present."""
    meta = _filters(widget_ds)["hours_band"]
    assert meta["type"] == "choice"
    assert [v for v, _ in meta["choices"]] == ["", "after_hours", "business"]


def test_datetime_computed_column_gets_presets_and_range(widget_ds, people):
    meta = _filters(widget_ds)["seen_at"]
    assert meta["type"] == "choice"
    assert meta["is_date"] is True
    assert ("week", "Past 7 days") in meta["choices"]
    assert meta["range"] is True  # half-open bounds advertised, as for model dates


def test_model_field_columns_are_unchanged(widget_ds, people):
    assert "username" in _filters(widget_ds)


# --- the cases we deliberately do NOT advertise ------------------------------


def test_aggregate_annotation_is_not_advertised(widget_ds, people):
    """A Count would need a GROUP BY over the table just to probe its domain,
    and the filter is a HAVING clause — offer nothing rather than pay that."""
    assert "login_count" not in _filters(widget_ds)


def test_high_cardinality_computed_string_is_not_advertised(widget_ds, db):
    """Too many distinct values for a dropdown, and a text box would promise
    substring matching the exact comparison won't deliver — so: no widget.
    The filter itself still works (asserted below)."""
    for i in range(_max() + 5):
        User.objects.create_user(f"user_{i:03d}", password="p")
    assert "handle" not in _filters(widget_ds)


def _max() -> int:
    return core.Dataset._COMPUTED_CHOICE_MAX


def test_unadvertised_computed_filter_still_applies(widget_ds, people):
    """The contract the omission rests on: not offered ≠ not supported."""
    assert widget_ds.count(filters={"handle": "@staff_a"}) == 1
    assert widget_ds.count(filters={"login_count": "0"}) == 3


# --- the round trip that makes the advertisement meaningful ------------------


@pytest.mark.parametrize("col", ["answered", "hours_band"])
def test_every_advertised_choice_actually_narrows(widget_ds, people, col):
    """A dropdown value the schema offers must work as a filter and select a
    strict subset — otherwise we are advertising a control that lies."""
    meta = _filters(widget_ds)[col]
    total = widget_ds.count()
    seen = 0
    for value, _label in meta["choices"]:
        if value == "":
            continue
        n = widget_ds.count(filters={col: value})
        assert 0 < n < total, f"{col}={value} selected {n} of {total}"
        seen += n
    assert seen == total, "the offered values should partition the rows"
