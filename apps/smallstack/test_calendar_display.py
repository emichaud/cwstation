"""CalendarDisplay per-day render cap and the "+N more" drill-down.

A busy month used to render one chip (plus a hover-tooltip subtree) per record,
so a high-volume site produced tens of thousands of DOM nodes and a calendar
that took seconds to paint. Cells now render at most `max_per_day` chips and
link the remainder into a single-day panel.

The invariant these tests protect: capping is a RENDERING limit only. Counts
stay exact, so the header total and each "+N more" badge still describe the real
data — a cap that quietly under-reports would be worse than the slow page.
"""

import datetime as dt

import pytest
from django.test import RequestFactory

from apps.smallstack.crud import Action, CRUDView
from apps.smallstack.displays import CalendarDisplay
from apps.smallstack.models import APIToken

pytestmark = pytest.mark.django_db

MONTH = "2026-08"
DAY = dt.date(2026, 8, 12)


class _Cfg(CRUDView):
    model = APIToken
    list_fields = ["name"]
    url_base = "cal-tokens"
    actions = [Action.LIST]  # no DETAIL → chips render unlinked, no reversing


def _ctx(display, **params):
    request = RequestFactory().get("/", params)
    return display.get_context(APIToken.objects.all(), _Cfg, request)


def _cell(context, day: int):
    for week in context["weeks"]:
        for cell in week:
            if cell and cell["day"] == day:
                return cell
    raise AssertionError(f"day {day} not in grid")


@pytest.fixture
def busy_day(db):
    """40 tokens all landing on the same day."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username="calowner", password="x")
    stamp = dt.datetime.combine(DAY, dt.time(9, 0), tzinfo=dt.UTC)
    for i in range(40):
        token, _ = APIToken.create_token(
            user=user, name=f"cal-{i:02d}", token_type="manual", access_level="read"
        )
        APIToken.objects.filter(pk=token.pk).update(created_at=stamp)
    return user


def _display(**kwargs):
    return CalendarDisplay(date_field="created_at", title_field="name", **kwargs)


def test_cell_renders_at_most_max_per_day(busy_day):
    context = _ctx(_display(max_per_day=5), month=MONTH)
    cell = _cell(context, DAY.day)
    assert len(cell["events"]) == 5


def test_overflow_count_reports_the_remainder(busy_day):
    context = _ctx(_display(max_per_day=5), month=MONTH)
    cell = _cell(context, DAY.day)
    assert cell["overflow_count"] == 35
    assert cell["total_count"] == 40


def test_header_total_is_not_distorted_by_the_cap(busy_day):
    """The cap must never make the calendar under-report how much data exists."""
    context = _ctx(_display(max_per_day=5), month=MONTH)
    assert context["event_count"] == 40


def test_max_per_day_none_renders_everything(busy_day):
    """Back-compat escape hatch for callers that want the old behavior."""
    context = _ctx(_display(max_per_day=None), month=MONTH)
    cell = _cell(context, DAY.day)
    assert len(cell["events"]) == 40
    assert cell["overflow_count"] == 0


def test_rendered_chip_count_is_bounded_by_volume(busy_day):
    """The whole point: chips rendered stay flat as records grow."""
    context = _ctx(_display(max_per_day=5), month=MONTH)
    rendered = sum(
        len(cell["events"]) for week in context["weeks"] for cell in week if cell
    )
    assert rendered <= 5 * 31
    assert rendered < context["event_count"]


# --------------------------------------------------------------------------
# The drill-down
# --------------------------------------------------------------------------


def test_selected_day_returns_the_full_uncapped_list(busy_day):
    context = _ctx(_display(max_per_day=5), month=MONTH, day=DAY.isoformat())
    assert context["selected_day"] == DAY
    assert len(context["selected_day_events"]) == 40
    # …while the grid cell for that day stays capped
    assert len(_cell(context, DAY.day)["events"]) == 5


def test_selected_day_marks_its_cell(busy_day):
    context = _ctx(_display(max_per_day=5), month=MONTH, day=DAY.isoformat())
    assert _cell(context, DAY.day)["is_selected"] is True
    assert _cell(context, DAY.day + 1)["is_selected"] is False


def test_no_day_param_means_no_panel(busy_day):
    context = _ctx(_display(max_per_day=5), month=MONTH)
    assert context["selected_day"] is None
    assert context["selected_day_events"] == []


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-99", "", "2026-09-01"])
def test_unparseable_or_out_of_month_day_is_ignored(busy_day, bad):
    """A hand-edited ?day= must not 500 or leak another month into the panel."""
    context = _ctx(_display(max_per_day=5), month=MONTH, day=bad)
    assert context["selected_day"] is None
    assert context["selected_day_events"] == []
