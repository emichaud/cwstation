"""MCPDashboardWidget tests."""

from unittest.mock import MagicMock, patch

import pytest

from apps.mcp.dashboard_widgets import MCPDashboardWidget

pytestmark = pytest.mark.django_db


def test_widget_metadata():
    w = MCPDashboardWidget()
    assert w.title == "MCP"
    assert w.url_name == "mcp_admin:health"
    assert "svg" in w.icon


def test_empty_registry_no_orphans_shows_awaiting_state(clean_registry):
    """0 tools + 0 orphan files = uninstalled-but-OK. NOT a warning."""
    with patch.object(MCPDashboardWidget, "_orphan_count", return_value=0):
        data = MCPDashboardWidget().get_data()
    assert data["headline"] == "No tools"
    assert data["detail"] == "Awaiting enable_mcp"
    assert data["status"] == "operational"


def test_populated_registry_no_orphans_shows_all_passing(clean_registry):
    """Tools registered + 0 orphans = green operational state."""
    from apps.mcp.factory import register_mcp_tools_from_crudview
    from apps.mcp.tests.fake_app.views import AutodiscoverWidgetCRUDView

    register_mcp_tools_from_crudview(AutodiscoverWidgetCRUDView)

    with patch.object(MCPDashboardWidget, "_orphan_count", return_value=0):
        data = MCPDashboardWidget().get_data()
    assert data["headline"].endswith(" tools") or data["headline"].endswith(" tool")
    assert data["detail"] == "All checks passing"
    assert data["status"] == "operational"


def test_orphan_files_show_degraded(clean_registry):
    """`enable_mcp = True` files not in the registry → WARN."""
    with patch.object(MCPDashboardWidget, "_orphan_count", return_value=2):
        data = MCPDashboardWidget().get_data()
    assert data["status"] == "degraded"
    assert "2 unregistered file" in data["detail"]


def test_single_tool_pluralization(clean_registry):
    """One tool says "1 tool" (singular), not "1 tools"."""
    from apps.mcp.server import TOOL_REGISTRY, ToolDef

    TOOL_REGISTRY["solo"] = ToolDef(name="solo", description="x", input_schema={})
    with patch.object(MCPDashboardWidget, "_orphan_count", return_value=0):
        data = MCPDashboardWidget().get_data()
    assert data["headline"] == "1 tool"


def test_api_extras_payload(clean_registry):
    """The API endpoint sees extra structured data."""
    from apps.mcp.server import TOOL_REGISTRY, ToolDef

    TOOL_REGISTRY["read_one"] = ToolDef(name="read_one", description="x", input_schema={}, write=False)
    TOOL_REGISTRY["write_one"] = ToolDef(name="write_one", description="x", input_schema={}, write=True)
    with patch.object(MCPDashboardWidget, "_orphan_count", return_value=0):
        extras = MCPDashboardWidget().get_api_extras()
    assert extras["tool_count"] == 2
    assert extras["read_tool_count"] == 1
    assert extras["write_tool_count"] == 1
    assert extras["orphan_count"] == 0


def test_orphan_count_is_safe_on_unexpected_failure():
    """If the doctor's scanner ever raises, the widget swallows it
    rather than blow up the entire dashboard."""
    with patch(
        "apps.mcp.management.commands.mcp_doctor.Command",
        new=MagicMock(side_effect=RuntimeError("scanner broke")),
    ):
        assert MCPDashboardWidget._orphan_count() == 0


def test_widget_is_registered_on_dashboard():
    """`apps/mcp/apps.py:ready()` calls dashboard.register(...) — so
    the widget should already be in the registry."""
    from apps.smallstack import dashboard

    titles = [w.title for w in dashboard._standalone_widgets]
    assert "MCP" in titles
