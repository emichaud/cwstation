"""Tests for the Activity page — groupby, threat panel, filterable log."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="activity-staff", password="p", email="s@example.com", is_staff=True
    )


def _seed_logs(n_ok=5, n_404=3, n_401=12):
    """Seed RequestLog rows for the three regions."""
    from apps.activity.models import RequestLog

    for _ in range(n_ok):
        RequestLog.objects.create(
            path="/api/orders/", method="GET", status_code=200,
            response_time_ms=10, ip_address="10.0.0.1", user_agent="curl/8.0",
        )
    for i in range(n_404):
        RequestLog.objects.create(
            path=f"/api/missing-{i}/", method="GET", status_code=404,
            response_time_ms=5, ip_address="10.0.0.2", user_agent="curl/8.0",
        )
    for _ in range(n_401):
        RequestLog.objects.create(
            path="/api/orders/", method="GET", status_code=401,
            response_time_ms=8, ip_address="198.51.100.99", user_agent="curl/8.0",
        )


def test_anonymous_blocked(client):
    resp = client.get(reverse("api_admin:activity"))
    assert resp.status_code in (302, 401, 403)


def test_staff_sees_activity_page(client, staff_user):
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:activity"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Top endpoints" in body
    assert "Threat signals" in body
    assert "Recent /api requests" in body


def test_per_endpoint_summary_aggregates(client, staff_user):
    _seed_logs(n_ok=5, n_404=3, n_401=12)
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:activity"))
    body = resp.content.decode()
    # The 401 burst path has 12 hits — it should be in the top 10.
    assert "/api/orders/" in body


def test_threat_panel_surfaces_auth_burst(client, staff_user):
    _seed_logs(n_ok=0, n_404=0, n_401=15)
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:activity"))
    body = resp.content.decode()
    # The HIGH badge + the IP should both appear.
    assert "HIGH" in body
    assert "198.51.100.99" in body
    assert "auth failures" in body


def test_threat_panel_empty_when_no_signals(client, staff_user):
    """Below-threshold activity should leave the panel showing the success copy."""
    _seed_logs(n_ok=3, n_404=2, n_401=0)
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:activity"))
    body = resp.content.decode()
    assert "No threat signals in the last 24h" in body


def test_filter_by_status_class_narrows_log(client, staff_user):
    _seed_logs(n_ok=5, n_404=3, n_401=0)
    client.force_login(staff_user)
    resp_4xx = client.get(reverse("api_admin:activity") + "?status_class=4xx")
    # Total count in the paginator reflects the filtered set — that's the
    # request log specifically (Region 3), not the per-endpoint summary.
    assert resp_4xx.context["paginator"].count == 3  # only the 3 404s

    resp_2xx = client.get(reverse("api_admin:activity") + "?status_class=2xx")
    assert resp_2xx.context["paginator"].count == 5  # only the 5 200s


def test_filter_by_ip(client, staff_user):
    _seed_logs(n_ok=5, n_404=3, n_401=12)
    client.force_login(staff_user)
    resp = client.get(reverse("api_admin:activity") + "?ip=198.51.100.99")
    body = resp.content.decode()
    assert "198.51.100.99" in body
    assert "10.0.0.1" not in body  # filtered out


def test_scanner_only_toggle(client, staff_user):
    from apps.activity.models import RequestLog

    RequestLog.objects.create(
        path="/api/x/", method="GET", status_code=200,
        response_time_ms=5, ip_address="10.0.0.5", user_agent="sqlmap/1.7",
    )
    RequestLog.objects.create(
        path="/api/y/", method="GET", status_code=200,
        response_time_ms=5, ip_address="10.0.0.6", user_agent="curl/8.0",
    )
    client.force_login(staff_user)
    resp_on = client.get(reverse("api_admin:activity") + "?scanner_only=on")
    # Filter applies to Region 3 (request log) only — the paginator count
    # is the truth-of-the-filter.
    assert resp_on.context["paginator"].count == 1
    resp_off = client.get(reverse("api_admin:activity"))
    assert resp_off.context["paginator"].count == 2
