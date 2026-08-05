"""TokensDashboardWidget tests."""

import pytest
from django.contrib.auth import get_user_model

from apps.smallstack.models import APIToken
from apps.tokenmgr.dashboard_widgets import TokensDashboardWidget

pytestmark = pytest.mark.django_db
User = get_user_model()


def test_empty_state():
    APIToken.objects.all().delete()
    data = TokensDashboardWidget().get_data()
    assert data["headline"] == "No tokens"
    assert data["status"] == "operational"
    assert "Mint" in data["detail"]


def test_active_only_headline():
    u = User.objects.create_user(username="x", password="x")
    APIToken.create_token(user=u, name="a")
    APIToken.create_token(user=u, name="b")
    data = TokensDashboardWidget().get_data()
    assert "2 active" in data["headline"]
    assert "of 2 total" in data["detail"]
    assert "revoked" not in data["detail"]


def test_revoked_count_in_detail():
    u = User.objects.create_user(username="x", password="x")
    t, _ = APIToken.create_token(user=u, name="a")
    t.revoke()
    APIToken.create_token(user=u, name="b")
    data = TokensDashboardWidget().get_data()
    assert "1 active" in data["headline"]
    assert "of 2 total" in data["detail"]
    assert "1 revoked" in data["detail"]


def test_api_extras_payload():
    u = User.objects.create_user(username="x", password="x")
    APIToken.create_token(user=u, name="r", access_level="readonly")
    APIToken.create_token(user=u, name="s", access_level="staff")
    APIToken.create_token(user=u, name="a", access_level="auth")
    extras = TokensDashboardWidget().get_api_extras()
    assert extras["total_tokens"] == 3
    assert extras["active_tokens"] == 3
    assert extras["revoked_tokens"] == 0
    assert extras["by_access_level"] == {"readonly": 1, "staff": 1, "auth": 1}


def test_widget_metadata():
    w = TokensDashboardWidget()
    assert w.title == "API Tokens"
    assert w.url_name == "tokenmgr:tokens-list"


def test_widget_registered_on_dashboard():
    from apps.smallstack import dashboard

    titles = [w.title for w in dashboard._standalone_widgets]
    assert "API Tokens" in titles
