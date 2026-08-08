"""HTTP surface for published feeds: ``/feed/<slug>.rss`` and ``.atom``.

Access mirrors search's :class:`~apps.search.access.SearchAccess`:

* ``anonymous`` — public, no auth (status pages, changelogs).
* ``authenticated`` — any signed-in user, or a valid API token.
* ``staff`` — staff users, or an API token whose user is staff.

Feed readers can't send a session cookie, so token-gated feeds accept the token
in the ``Authorization: Bearer`` header **or** a ``?token=`` query param (the
latter is what a reader can embed in the subscribe URL).
"""

from __future__ import annotations

from typing import Any

from django.http import Http404, HttpRequest, HttpResponse

from apps.search.access import SearchAccess

from .base import Feed
from .registry import get_feed
from .render import render_feed


def _effective_user(request: HttpRequest) -> Any:
    """Session user, or the user behind a supplied API token (header or query)."""
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        return request.user
    raw = ""
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
    if not raw:
        raw = request.GET.get("token", "").strip()
    if not raw:
        return request.user  # AnonymousUser
    try:
        from apps.smallstack.models import APIToken

        user, token = APIToken.authenticate(raw)
        if user is not None:
            return user
    except Exception:
        pass
    return request.user


def _authorized(feed: Feed, user: Any) -> bool:
    level = getattr(feed, "access", SearchAccess.STAFF)
    if level == SearchAccess.ANONYMOUS:
        return True
    if level == SearchAccess.AUTHENTICATED:
        return bool(getattr(user, "is_authenticated", False))
    # STAFF (secure default for anything unexpected).
    return bool(getattr(user, "is_staff", False))


def feed_view(request: HttpRequest, slug: str, fmt: str = "rss") -> HttpResponse:
    feed = get_feed(slug)
    if feed is None:
        raise Http404("No such feed")

    user = _effective_user(request)
    if not _authorized(feed, user):
        # 401 (not 403): tells a reader auth is required and it may retry with
        # a token, rather than treating the feed as gone.
        resp = HttpResponse("Authentication required for this feed.", status=401)
        resp["WWW-Authenticate"] = 'Bearer realm="feeds"'
        return resp

    xml, content_type = render_feed(feed, request, "atom" if fmt == "atom" else "rss")
    return HttpResponse(xml, content_type=content_type)
