"""Mint flow — form gating, access-level restrictions, reveal handoff."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.smallstack.models import APIToken
from apps.tokenmgr.forms import TokenCreateForm
from apps.tokenmgr.views import REVEAL_SESSION_KEY

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def alice():
    return User.objects.create_user(username="alice", password="x")


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="boss", password="x", is_staff=True)


@pytest.fixture
def superuser():
    return User.objects.create_superuser(username="root", password="x", email="r@x")


def test_anonymous_create_redirects_to_login():
    resp = Client().get(reverse("tokenmgr:token-create"), HTTP_HOST="localhost")
    assert resp.status_code in (301, 302)


def test_non_staff_form_locks_user_field_and_hides_picker(alice):
    form = TokenCreateForm(request_user=alice)
    # User picker is HiddenInput for non-staff
    assert form.fields["user"].widget.__class__.__name__ == "HiddenInput"
    # Only readonly is offered
    choices = dict(form.fields["access_level"].choices)
    assert list(choices.keys()) == ["readonly"]
    assert form.fields["user"].initial == alice.pk


def test_staff_form_offers_staff_and_readonly_not_auth(staff_user):
    form = TokenCreateForm(request_user=staff_user)
    choices = dict(form.fields["access_level"].choices)
    assert set(choices.keys()) == {"staff", "readonly"}
    assert "auth" not in choices


def test_superuser_form_offers_all_levels(superuser):
    form = TokenCreateForm(request_user=superuser)
    choices = dict(form.fields["access_level"].choices)
    assert set(choices.keys()) == {"auth", "staff", "readonly"}


def test_non_staff_cannot_submit_staff_level(alice):
    form = TokenCreateForm(
        data={"user": alice.pk, "name": "x", "access_level": "staff", "description": ""},
        request_user=alice,
    )
    assert not form.is_valid()
    assert "access_level" in form.errors


def test_non_staff_cannot_mint_for_other_user(alice):
    other = User.objects.create_user(username="other", password="x")
    form = TokenCreateForm(
        data={"user": other.pk, "name": "x", "access_level": "readonly", "description": ""},
        request_user=alice,
    )
    # `user` field's queryset is narrowed to alice → other.pk doesn't validate.
    assert not form.is_valid()


def test_staff_can_mint_staff_level_for_self(staff_user):
    form = TokenCreateForm(
        data={"user": staff_user.pk, "name": "ci", "access_level": "staff", "description": ""},
        request_user=staff_user,
    )
    assert form.is_valid(), form.errors


def test_non_superuser_cannot_mint_auth_level(staff_user):
    form = TokenCreateForm(
        data={"user": staff_user.pk, "name": "x", "access_level": "auth", "description": ""},
        request_user=staff_user,
    )
    assert not form.is_valid()
    assert "access_level" in form.errors


def test_post_creates_token_and_redirects_to_reveal(alice):
    c = Client(enforce_csrf_checks=False)
    c.force_login(alice)
    resp = c.post(
        reverse("tokenmgr:token-create"),
        {"user": alice.pk, "name": "first", "access_level": "readonly", "description": ""},
        HTTP_HOST="localhost",
    )
    assert resp.status_code == 302
    # Token row exists for alice
    token = APIToken.objects.get(name="first", user=alice)
    assert token.access_level == "readonly"
    # Reveal key stashed in session
    assert REVEAL_SESSION_KEY in c.session
    # Redirect points at the reveal page
    assert reverse("tokenmgr:token-reveal", kwargs={"pk": token.pk}) in resp["Location"]


def test_reveal_shows_key_once(alice):
    c = Client(enforce_csrf_checks=False)
    c.force_login(alice)
    c.post(
        reverse("tokenmgr:token-create"),
        {"user": alice.pk, "name": "shown-once", "access_level": "readonly", "description": ""},
        HTTP_HOST="localhost",
    )
    token = APIToken.objects.get(name="shown-once")

    # First GET to reveal page shows the key
    first = c.get(reverse("tokenmgr:token-reveal", kwargs={"pk": token.pk}), HTTP_HOST="localhost")
    assert first.status_code == 200
    assert "raw_key" in first.context
    assert REVEAL_SESSION_KEY not in c.session

    # Second GET redirects to list — key is gone
    second = c.get(reverse("tokenmgr:token-reveal", kwargs={"pk": token.pk}), HTTP_HOST="localhost")
    assert second.status_code == 302
    assert reverse("tokenmgr:tokens-list") in second["Location"]


def test_reveal_forbidden_for_other_owner(alice):
    other = User.objects.create_user(username="other", password="x")
    t, _ = APIToken.create_token(user=other, name="not-yours")
    c = Client()
    c.force_login(alice)
    resp = c.get(reverse("tokenmgr:token-reveal", kwargs={"pk": t.pk}), HTTP_HOST="localhost")
    assert resp.status_code == 403
