"""View mixins for self-service token management."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin


class LoginRequiredAuthMixin(LoginRequiredMixin):
    """Authenticated users only — no staff requirement.

    Used as the base auth gate for token management views. Combined with
    `_owner_or_staff(...)` helpers in views to enforce per-row ownership.
    """


def is_owner_or_staff(user, token) -> bool:
    """Return True if `user` may view/revoke `token`."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff:
        return True
    return token.user_id == user.pk
