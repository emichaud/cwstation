"""List + detail views — auth gating and per-row ownership."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.smallstack.models import APIToken

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def alice():
    return User.objects.create_user(username="alice", password="x")


@pytest.fixture
def bob():
    return User.objects.create_user(username="bob", password="x")


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="boss", password="x", is_staff=True)


def _mint(user, **kwargs):
    kwargs.setdefault("name", f"{user.username}-test")
    kwargs.setdefault("access_level", "readonly")
    token, _raw = APIToken.create_token(user=user, **kwargs)
    return token


def test_list_anonymous_redirects_to_login():
    resp = Client().get(reverse("tokenmgr:tokens-list"), HTTP_HOST="localhost")
    assert resp.status_code in (301, 302)


def test_list_non_staff_sees_only_their_tokens(alice, bob):
    _mint(alice, name="alice-1")
    _mint(alice, name="alice-2")
    _mint(bob, name="bob-1")
    c = Client()
    c.force_login(alice)
    resp = c.get(reverse("tokenmgr:tokens-list"), HTTP_HOST="localhost")
    assert resp.status_code == 200
    names = [t.name for t in resp.context["object_list"]]
    assert "alice-1" in names
    assert "alice-2" in names
    assert "bob-1" not in names


def test_list_staff_sees_every_token(alice, bob, staff_user):
    _mint(alice, name="alice-token")
    _mint(bob, name="bob-token")
    c = Client()
    c.force_login(staff_user)
    resp = c.get(reverse("tokenmgr:tokens-list"), HTTP_HOST="localhost")
    names = [t.name for t in resp.context["object_list"]]
    assert "alice-token" in names
    assert "bob-token" in names


def test_list_overview_stats_scoped_for_non_staff(alice, bob):
    _mint(alice, name="alice-a")
    _mint(alice, name="alice-b")
    _mint(bob, name="bob-a")
    c = Client()
    c.force_login(alice)
    resp = c.get(reverse("tokenmgr:tokens-list"), HTTP_HOST="localhost")
    stats = resp.context["overview_stats"]
    assert stats["total_tokens"] == 2  # only alice's


def test_list_overview_stats_global_for_staff(alice, bob, staff_user):
    _mint(alice)
    _mint(bob)
    c = Client()
    c.force_login(staff_user)
    resp = c.get(reverse("tokenmgr:tokens-list"), HTTP_HOST="localhost")
    assert resp.context["overview_stats"]["total_tokens"] == 2


def test_detail_owner_can_view(alice):
    t = _mint(alice)
    c = Client()
    c.force_login(alice)
    resp = c.get(reverse("tokenmgr:tokens-detail", kwargs={"pk": t.pk}), HTTP_HOST="localhost")
    assert resp.status_code == 200


def test_detail_non_owner_non_staff_is_forbidden(alice, bob):
    t = _mint(alice)
    c = Client()
    c.force_login(bob)
    resp = c.get(reverse("tokenmgr:tokens-detail", kwargs={"pk": t.pk}), HTTP_HOST="localhost")
    assert resp.status_code == 403


def test_detail_staff_can_view_any(alice, staff_user):
    t = _mint(alice)
    c = Client()
    c.force_login(staff_user)
    resp = c.get(reverse("tokenmgr:tokens-detail", kwargs={"pk": t.pk}), HTTP_HOST="localhost")
    assert resp.status_code == 200


def test_detail_renders_usage_panel(alice):
    t = _mint(alice)
    c = Client()
    c.force_login(alice)
    resp = c.get(reverse("tokenmgr:tokens-detail", kwargs={"pk": t.pk}), HTTP_HOST="localhost")
    assert "token_stats" in resp.context
    assert "Usage" in resp.content.decode()
