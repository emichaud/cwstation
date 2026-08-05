"""Tests for the threat heuristics in apps/api/threats.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _seed(n, *, path="/api/orders/", status=401, ip="198.51.100.7", ua="curl/8.0"):
    from apps.activity.models import RequestLog

    for _ in range(n):
        RequestLog.objects.create(
            path=path, method="GET", status_code=status,
            response_time_ms=10, ip_address=ip, user_agent=ua,
        )


def test_auth_failure_burst_fires_at_threshold():
    from apps.api.threats import detect_auth_failure_burst

    _seed(10)  # exactly the default threshold
    signals = detect_auth_failure_burst()
    assert len(signals) == 1
    assert signals[0].severity == "high"
    assert signals[0].ip == "198.51.100.7"
    assert signals[0].count == 10


def test_auth_failure_burst_no_false_positive_at_minus_one():
    from apps.api.threats import detect_auth_failure_burst

    _seed(9)  # one under threshold
    assert detect_auth_failure_burst() == []


def test_auth_failure_burst_only_for_api_paths():
    from apps.api.threats import detect_auth_failure_burst

    _seed(15, path="/admin/login/")  # NOT /api/
    assert detect_auth_failure_burst() == []


def test_path_scanning_requires_both_thresholds():
    from apps.api.threats import detect_path_scanning

    # 25 distinct paths but only 3 are 404s — below the 404 threshold.
    for i in range(25):
        _seed(1, path=f"/api/foo-{i}/", status=200, ip="10.0.0.1")
    for i in range(3):
        _seed(1, path=f"/api/bar-{i}/", status=404, ip="10.0.0.1")
    assert detect_path_scanning() == []


def test_path_scanning_fires_with_many_404s():
    from apps.api.threats import detect_path_scanning

    for i in range(25):
        _seed(1, path=f"/api/scan-{i}/", status=404, ip="10.0.0.2")
    signals = detect_path_scanning()
    assert len(signals) == 1
    assert signals[0].severity == "medium"
    assert signals[0].ip == "10.0.0.2"


def test_scanner_user_agent_detects_sqlmap():
    from apps.api.threats import detect_scanner_user_agents

    _seed(1, ua="sqlmap/1.7.2#stable (http://sqlmap.org)", ip="203.0.113.42")
    signals = detect_scanner_user_agents()
    assert len(signals) == 1
    assert signals[0].severity == "medium"
    assert "sqlmap" in signals[0].label.lower()
    assert signals[0].ip == "203.0.113.42"


def test_scanner_user_agent_detects_dirbuster():
    from apps.api.threats import detect_scanner_user_agents

    _seed(5, ua="DirBuster-1.0-RC1 (http://www.owasp.org)", ip="203.0.113.43")
    signals = detect_scanner_user_agents()
    assert len(signals) == 1
    assert "dirbuster" in signals[0].label.lower()


def test_scanner_user_agent_no_match_for_curl():
    from apps.api.threats import detect_scanner_user_agents

    _seed(50, ua="curl/8.0")
    assert detect_scanner_user_agents() == []


def test_revoked_token_use_fires():
    from django.contrib.auth import get_user_model

    from apps.activity.models import RequestLog
    from apps.api.threats import detect_revoked_token_use
    from apps.smallstack.models import APIToken

    user = get_user_model().objects.create_user(
        username="revoked-test", password="p", email="r@example.com"
    )
    token, raw = APIToken.create_token(user=user, name="t1", access_level="readonly")
    token.revoke()  # is_active=False
    RequestLog.objects.create(
        path="/api/orders/", method="GET", status_code=401,
        response_time_ms=10, ip_address="10.0.0.10", user_agent="curl/8.0",
        api_token=token,
    )
    signals = detect_revoked_token_use()
    assert len(signals) == 1
    assert signals[0].severity == "low"
    assert "Revoked token" in signals[0].label


def test_collect_threats_orders_by_severity():
    from apps.api.threats import collect_threats

    # Seed: one HIGH (auth burst) + one MEDIUM (scanner UA).
    _seed(15, ip="1.1.1.1")  # auth burst → HIGH
    _seed(1, ua="sqlmap/1.7", ip="2.2.2.2", status=200)  # → MEDIUM
    signals = collect_threats()
    assert len(signals) >= 2
    # HIGH must come first.
    assert signals[0].severity == "high"
    assert signals[1].severity == "medium"


def test_count_high_severity_threats():
    from apps.api.threats import count_high_severity_threats

    assert count_high_severity_threats() == 0
    _seed(15, ip="3.3.3.3")  # auth burst → HIGH
    assert count_high_severity_threats() == 1
