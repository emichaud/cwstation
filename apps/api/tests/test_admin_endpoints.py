"""Tests for the /smallstack/api/endpoints/ admin page (services + resources)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="ep-staff", password="p", email="s@example.com", is_staff=True
    )


@pytest.fixture
def regular_user():
    User = get_user_model()
    return User.objects.create_user(username="ep-regular", password="p", email="r@example.com")


def test_anonymous_user_redirected(client):
    resp = client.get(reverse("api_admin:endpoints"))
    assert resp.status_code in (302, 401, 403)


def test_non_staff_user_blocked(client, regular_user):
    client.force_login(regular_user)
    resp = client.get(reverse("api_admin:endpoints"))
    assert resp.status_code in (302, 403)


def test_staff_sees_services_and_resources(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:endpoints"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "API Services" in body
    assert "API Resources" in body
    # Service links resolve to the live REST routes.
    assert reverse("api-docs") in body  # Swagger
    assert reverse("api-redoc") in body  # ReDoc
    assert reverse("api-openapi-schema") in body  # OpenAPI JSON


def test_nav_exposes_endpoints_tab(client, staff_user):
    client.force_login(staff_user)
    body = client.get(reverse("api_admin:endpoints")).content.decode()
    # All three tab hrefs are present in the section nav.
    assert reverse("api_admin:health") in body
    assert reverse("api_admin:endpoints") in body
    assert reverse("api_admin:activity") in body
    # Endpoints tab is the active one on this page.
    assert 'class="tab-btn active"' in body


def test_empty_registry_shows_guidance(client, staff_user, monkeypatch):
    # Base ships 0 enable_api CRUDViews → empty-state guidance.
    monkeypatch.setattr("apps.smallstack.api._api_registry", [])
    client.force_login(staff_user)
    body = client.get(reverse("api_admin:endpoints")).content.decode()
    assert "enable_api = True" in body
    assert "API surface is empty" in body


def test_populated_registry_lists_resource(client, staff_user, monkeypatch):
    User = get_user_model()
    fake_config = SimpleNamespace(model=User)
    monkeypatch.setattr("apps.smallstack.api._api_registry", [(fake_config, "fake-list")])
    monkeypatch.setattr(
        "apps.smallstack.api._build_endpoint_schema",
        lambda cfg, name: {
            "model": "User",
            "url": "/api/accounts/user/",
            "methods": ["GET", "POST"],
            "filter_fields": ["is_staff", "is_active"],
            "search_fields": ["username"],
        },
    )
    client.force_login(staff_user)
    body = client.get(reverse("api_admin:endpoints")).content.decode()
    assert "/api/accounts/user/" in body
    assert "GET" in body and "POST" in body
    # Resource count surfaces in the header + nav badge.
    assert "API Resources" in body


def test_health_links_to_endpoints_and_swagger(client, staff_user):
    client.force_login(staff_user)
    body = client.get(reverse("api_admin:health")).content.decode()
    # The "Registered endpoints" stat now links to the Endpoints tab.
    assert reverse("api_admin:endpoints") in body
    # Quick Swagger link in the header.
    assert "Open Swagger" in body
