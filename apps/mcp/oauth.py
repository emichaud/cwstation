"""OAuth 2.0 PKCE + URL-construction helpers for the MCP server.

PKCE is S256-only — `plain` is rejected because Claude.ai uses S256 and
allowing plain would silently weaken every other client.

issuer_url(request) honors HTTP_X_FORWARDED_PROTO before reading
request.is_secure() because, behind kamal-proxy / nginx / ALB, the WSGI
worker sees the inner http:// and would otherwise advertise an http://
issuer to clients that reject it.
"""

from __future__ import annotations

import base64
import hashlib

from django.http import HttpRequest


def verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """RFC 7636 verifier check. S256 only — `plain` rejected.

    Empty inputs are rejected (some clients send "" rather than omitting).
    """
    if not code_verifier or not code_challenge:
        return False
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


def issuer_url(request: HttpRequest) -> str:
    """Build the `https://host` issuer URL the metadata advertises.

    Reads HTTP_X_FORWARDED_PROTO first so a TLS-terminating proxy keeps the
    issuer on https://. Falls back to request.is_secure() then http://.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_PROTO", "").strip().lower()
    if forwarded:
        scheme = forwarded.split(",")[0].strip()
    elif request.is_secure():
        scheme = "https"
    else:
        scheme = "http"

    host = request.get_host()
    return f"{scheme}://{host}"


def absolute_url(request: HttpRequest, path: str) -> str:
    """Compose `issuer_url(request) + path`. Use for metadata fields."""
    if not path.startswith("/"):
        path = "/" + path
    return issuer_url(request) + path
