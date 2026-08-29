"""Typing helpers for the CW app's request handlers.

Two facts about this codebase that mypy can't see on its own:

1. ``@api_view`` parses the request body and attaches it as ``request.json``.
   Django's ``HttpRequest`` has no such attribute, so annotated handlers need a
   type that says it does.
2. Every CW endpoint is either ``@api_view(require_auth=True)`` or behind
   ``LoginRequiredMixin``, so ``request.user`` is always a real, saved user —
   but django-stubs types it ``User | AnonymousUser``, which fails on every ORM
   call (``filter(user=...)``, ``QSO(user=...)``).

The alternative was to drop the annotations entirely — with
``check_untyped_defs = false`` an unannotated body isn't checked at all, which
is how the framework's own handlers pass. That trades a real check for a green
lane, so these exist instead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.http import HttpRequest

if TYPE_CHECKING:
    # Annotation-only: no runtime import, so nothing here bypasses the
    # swappable-user-model rule. django-stubs resolves the ORM's expected type
    # from AUTH_USER_MODEL to exactly this class, so it's what the signatures
    # have to name to type-check.
    from apps.accounts.models import User

__all__ = ["APIRequest", "operator"]


class APIRequest(HttpRequest):
    """An ``HttpRequest`` as ``@api_view`` hands it to a handler.

    Type-only: never instantiated, only used in annotations. ``json`` is the
    decoded request body (``Any`` because a handler validates its own shape —
    every one of them starts with an ``isinstance(data, dict)`` check).
    """

    json: Any


def operator(request: HttpRequest) -> User:
    """The signed-in operator, typed concretely so ORM calls type-check.

    Safe because every caller sits behind ``require_auth=True`` or
    ``LoginRequiredMixin`` — an anonymous request is refused before the handler
    runs. Narrowing with ``cast`` rather than ``assert`` keeps that guarantee
    where it's actually enforced (the decorator) instead of restating it at
    every call site.
    """
    return cast("User", request.user)
