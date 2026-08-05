"""Test-only views module for the autodiscover test.

This file is intentionally NOT imported by any conftest.py or __init__.py.
The autodiscover test imports it via the same mechanism MCPConfig.ready()
uses (importlib + apps.get_app_configs() walk), proving that a CRUDView
defined here gets picked up without an explicit `from . import views`.
"""

from django import forms

from apps.mcp.tests.models import Widget
from apps.smallstack.crud import Action, CRUDView


class FakeAutodiscoverForm(forms.ModelForm):
    class Meta:
        model = Widget
        fields = ["name", "owner"]


class AutodiscoverWidgetCRUDView(CRUDView):
    """Defined here ON PURPOSE — must NOT be imported anywhere else for the
    test to be meaningful. If you find yourself adding `from . import views`
    to a conftest to make a test pass, you're testing the wrong thing.
    """

    model = Widget
    fields = ["name", "owner"]
    list_fields = ["name", "owner"]
    detail_fields = ["name", "owner"]
    url_base = "autodiscover_widgets"
    actions = [Action.LIST, Action.DETAIL]
    enable_mcp = True
    mcp_description = "Autodiscover-only widgets."
    form_class = FakeAutodiscoverForm
