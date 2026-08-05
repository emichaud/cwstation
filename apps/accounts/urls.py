"""URLs for invite-by-email and passwordless login (mounted under accounts/)."""

from django.urls import path

from .views import AcceptInviteView, InviteUserView, PasswordlessLoginView

app_name = "accounts"

urlpatterns = [
    path("invite/", InviteUserView.as_view(), name="invite"),
    path("invite/<uidb64>/<token>/", AcceptInviteView.as_view(), name="invite_accept"),
    path("login/code/", PasswordlessLoginView.as_view(), name="passwordless_login"),
]
