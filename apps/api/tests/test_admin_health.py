"""Tests for the /smallstack/api/ admin Health page + Self-Test endpoint."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="api-staff", password="p", email="s@example.com", is_staff=True
    )


@pytest.fixture
def regular_user():
    User = get_user_model()
    return User.objects.create_user(
        username="api-regular", password="p", email="r@example.com"
    )


def test_anonymous_user_redirected_to_login(client):
    resp = client.get(reverse("api_admin:health"))
    assert resp.status_code in (302, 401, 403)


def test_non_staff_user_blocked(client, regular_user):
    client.force_login(regular_user)
    resp = client.get(reverse("api_admin:health"))
    assert resp.status_code in (302, 403)


def test_staff_user_sees_health_page(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:health"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Diagnostics" in body
    assert "Run Self-Test" in body
    assert "Registered endpoints" in body
    # At least one PASS badge from the actual checks.
    assert "✓ PASS" in body


def test_health_page_shows_all_check_categories(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:health"))
    body = resp.content.decode()
    # The check names should all surface as <td> contents.
    for name in [
        "openapi-spec-validator",
        "Installed apps",
        "API registry",
        "URL conf",
        "Swagger / ReDoc shells",
        "OpenAPI validity",
        "Endpoint consistency",
        "Orphan files",
        "APIToken inventory",
    ]:
        assert name in body, f"missing diagnostic row: {name}"


def test_self_test_get_returns_405(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:self_test"))
    assert resp.status_code == 405


def test_self_test_post_returns_fragment(client, staff_user):
    client.force_login(staff_user)
    resp = client.post(reverse("api_admin:self_test"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Self-test result" in body
    # Should be either PASS or FAIL, not blank.
    assert "PASS" in body or "FAIL" in body


def test_self_test_blocked_for_non_staff(client, regular_user):
    client.force_login(regular_user)
    resp = client.post(reverse("api_admin:self_test"))
    assert resp.status_code in (302, 403)


def test_health_page_includes_nav_tabs(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:health"))
    body = resp.content.decode()
    # Both tabs should be present.
    assert ">Health" in body
    assert ">Activity" in body
