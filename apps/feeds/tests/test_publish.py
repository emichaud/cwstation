"""Publish-side tests — ModelFeed derivation, rendering, access gating, hooks."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.test import Client, RequestFactory

from apps.feeds.base import Feed, FeedItem
from apps.feeds.model_feed import ModelFeed
from apps.feeds.models import CollectedItem
from apps.feeds.registry import register_feed, unregister
from apps.feeds.render import render_feed
from apps.search.access import SearchAccess

pytestmark = pytest.mark.django_db


class _FakeView:
    """Stand-in CRUDView: ModelFeed only reads class attributes + .model."""

    model = CollectedItem
    url_base = "collected/items"
    search_display = "title"
    search_subtitle = "summary"
    rss_date_field = "published"

    def rss_item_extra(self, obj):
        return {"categories": ("news",)}


def _mk(title: str, when: datetime) -> CollectedItem:
    return CollectedItem.objects.create(
        source="t", guid=f"g-{title}", title=title, summary=f"about {title}", published=when
    )


def test_model_feed_derives_config_from_view():
    feed = ModelFeed(_FakeView)
    assert feed.slug == "collected-items"  # url_base slashes → dashes
    assert feed.title_field == "title"
    assert feed.description_field == "summary"
    assert feed.date_field == "published"
    assert feed.ordering == ["-published"]
    assert feed.access == SearchAccess.STAFF  # secure default


def test_model_feed_items_and_ordering():
    _mk("older", datetime(2021, 1, 1, tzinfo=timezone.utc))
    _mk("newer", datetime(2022, 1, 1, tzinfo=timezone.utc))
    feed = ModelFeed(_FakeView)
    items = list(feed.items(RequestFactory().get("/")))
    assert [i.title for i in items] == ["newer", "older"]  # -published
    assert items[0].description == "about newer"
    assert items[0].unique_id == f"feeds.CollectedItem:{CollectedItem.objects.get(title='newer').pk}"


def test_rss_item_extra_flows_into_feed_item():
    _mk("x", datetime(2022, 1, 1, tzinfo=timezone.utc))
    item = list(ModelFeed(_FakeView).items(RequestFactory().get("/")))[0]
    assert item.extra_kwargs == {"categories": ("news",)}


def test_render_produces_rss_and_atom():
    _mk("hello", datetime(2022, 1, 1, tzinfo=timezone.utc))
    feed = ModelFeed(_FakeView)
    req = RequestFactory(HTTP_HOST="testserver").get("/feed/collected-items.rss")
    rss, ct_rss = render_feed(feed, req, "rss")
    atom, ct_atom = render_feed(feed, req, "atom")
    assert "rss+xml" in ct_rss and "<rss" in rss and "hello" in rss
    assert "atom+xml" in ct_atom and "<feed" in atom


# --- access gating through the HTTP view -------------------------------------


class _PublicFeed(Feed):
    slug = "t-public"
    title = "Public"
    access = SearchAccess.ANONYMOUS

    def items(self, request):
        return [FeedItem(title="hi", link="/x", unique_id="1")]


class _StaffFeed(Feed):
    slug = "t-staff"
    title = "Staff"
    access = SearchAccess.STAFF

    def items(self, request):
        return [FeedItem(title="secret", link="/y", unique_id="2")]


@pytest.fixture
def _feeds():
    register_feed(_PublicFeed())
    register_feed(_StaffFeed())
    yield
    unregister("t-public")
    unregister("t-staff")


def test_anonymous_feed_is_public(_feeds):
    resp = Client().get("/feed/t-public.rss")
    assert resp.status_code == 200
    assert "rss+xml" in resp["Content-Type"]


def test_staff_feed_401s_for_anonymous(_feeds):
    resp = Client().get("/feed/t-staff.rss")
    assert resp.status_code == 401
    assert resp["WWW-Authenticate"].startswith("Bearer")


def test_staff_feed_served_to_staff_session(_feeds, django_user_model):
    django_user_model.objects.create_user(username="boss", password="pw", is_staff=True)
    client = Client()
    client.login(username="boss", password="pw")
    assert client.get("/feed/t-staff.rss").status_code == 200


def test_unknown_feed_404s():
    assert Client().get("/feed/does-not-exist.rss").status_code == 404
