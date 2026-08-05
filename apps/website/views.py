"""
Website views - customize these for your project.

This app is the designated place for project-specific pages like
your homepage, landing pages, about page, etc.

These pages are intentionally separated from SmallStack core so you
can customize them freely without conflicts when pulling upstream updates.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


def search_view(request: HttpRequest) -> HttpResponse:
    """Public-site search.

    Open to everyone — including signed-out visitors. The registry
    applies the per-view ``search_access`` gate (and the optional
    ``search_visibility`` callback) given the request user, so the
    *page* is public but the *data* a given visitor can find is
    determined by what each CRUDView opted into:

      - Anonymous visitors see help docs plus any CRUDView with
        ``search_access = SearchAccess.ANONYMOUS``.
      - Authenticated users additionally see CRUDViews opted in to
        ``SearchAccess.AUTHENTICATED`` (often filtered per user via
        ``search_visibility``).
      - Staff see every registered view.

    The recipes — and an Inventory app walkthrough — are in
    ``apps/smallstack/docs/search.md``.
    """
    # Imported lazily so collectstatic / migrate-only invocations don't
    # trigger search backend initialization.
    from apps.search.registry import get_indexed_sources, search_all, view_count
    from apps.search.views import group_by_model

    query = (request.GET.get("q") or "").strip()
    limit_per_model = int(request.GET.get("limit_per_model") or 10)

    ctx: dict[str, Any] = {
        "query": query,
        "registered_models": view_count(),
        "indexed_sources": get_indexed_sources(user=request.user),
    }
    if query:
        hits = search_all(query, limit_per_model=limit_per_model, user=request.user)
        ctx["grouped"] = group_by_model(hits)
        ctx["total_hits"] = len(hits)
    else:
        ctx["grouped"] = []
        ctx["total_hits"] = 0
    return render(request, "website/search.html", ctx)


def home_view(request: HttpRequest) -> HttpResponse:
    """CW Station's home is the CW Monitor. Kept as `website:home` (redirect)
    so framework templates that link 'Home' keep working."""
    return redirect("cw-monitor")
