"""issuer_url honors HTTP_X_FORWARDED_PROTO before request.is_secure()."""

from django.http import HttpRequest

from apps.mcp.oauth import issuer_url


def test_xfp_https_wins_over_insecure_request():
    req = HttpRequest()
    req.META = {"HTTP_X_FORWARDED_PROTO": "https", "HTTP_HOST": "localhost"}
    assert issuer_url(req) == "https://localhost"


def test_falls_back_to_http_without_xfp():
    req = HttpRequest()
    req.META = {"HTTP_HOST": "localhost"}
    assert issuer_url(req).startswith("http://")
