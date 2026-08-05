"""
Forms for the accounts app.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm

User = get_user_model()


class StyledSetPasswordForm(SetPasswordForm):
    """Django's SetPasswordForm with the SmallStack input class applied."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "vTextField"


class InviteUserForm(forms.Form):
    """Invite a new user by email. They set their own password via the link."""

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"class": "vTextField", "autofocus": True}),
    )
    first_name = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"class": "vTextField"})
    )
    last_name = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"class": "vTextField"})
    )
    is_staff = forms.BooleanField(
        required=False,
        label="Grant staff access",
        help_text="Staff can reach the admin tools area.",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email


class SignupForm(UserCreationForm):
    """
    Form for user registration.
    Uses username and password only - no email required to activate.
    """

    class Meta:
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add CSS classes for styling
        for field_name, field in self.fields.items():
            field.widget.attrs["class"] = "vTextField"
