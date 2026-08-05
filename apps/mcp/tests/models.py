"""Test-only models for the MCP test suite.

Widget and Gadget exercise the CRUDView factory without coupling the test
suite to any project-specific model. They're declared `managed=False` so
Django's migration framework leaves them alone; the conftest creates the
backing tables via schema_editor at session start.
"""

from django.conf import settings
from django.db import models


class Widget(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_test_widgets",
    )

    class Meta:
        app_label = "mcp_server"
        managed = False

    def __str__(self) -> str:
        return self.name


class Gadget(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_test_gadgets",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "mcp_server"
        managed = False

    def __str__(self) -> str:
        return self.name
