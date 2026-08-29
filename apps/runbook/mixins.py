"""Reusable view mixins for smallstack-runbook."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Mixin that restricts access to staff users.

    If the user is authenticated but not staff, raises 403 instead of
    redirecting to the login page (which is confusing when already logged in).
    """

    # Supplied by the View this mixin is always combined with.
    request: HttpRequest

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def handle_no_permission(self) -> HttpResponseRedirect:
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()
