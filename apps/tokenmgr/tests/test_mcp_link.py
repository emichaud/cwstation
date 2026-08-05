"""MCP consent page now deep-links to tokenmgr instead of Explorer."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db
User = get_user_model()


def test_consent_page_links_to_tokenmgr():
    """When apps.tokenmgr is installed, the MCP consent page surfaces
    /smallstack/tokens/ for users to manage their newly-minted token."""
    u = User.objects.create_user(username="oauth-user", password="x")
    c = Client()
    c.force_login(u)
    resp = c.get(
        "/mcp/oauth/authorize"
        "?client_id=mcp_x&redirect_uri=https://claude.ai/cb"
        "&code_challenge=abc&code_challenge_method=S256&state=s1",
        HTTP_HOST="localhost",
    )
    assert resp.status_code == 200
    assert "/smallstack/tokens/" in resp.content.decode()


def test_consent_page_falls_back_to_explorer_when_tokenmgr_missing():
    """If tokenmgr isn't installed (downstream choice), the link
    fallback is the staff-only Explorer surface."""
    from django.urls import NoReverseMatch

    from apps.mcp.oauth_views import AuthorizeView

    # `reverse` is imported locally inside _tokens_url, so patch at
    # the django.urls source.
    with patch("django.urls.reverse", side_effect=NoReverseMatch("missing")):
        assert AuthorizeView._tokens_url() == "/explorer/auth/apitoken/"
