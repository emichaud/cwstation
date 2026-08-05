"""URL config for the MCP admin web pages.

Mounted at ``/smallstack/mcp/`` via apps/smallstack/site_urls.py. Separate
from the JSON-RPC + OAuth surface in apps/mcp/urls.py so the admin UI
stays scoped to ``smallstack/`` and the protocol endpoints stay at root.
"""

from django.urls import path

from .admin_views import (
    MCPAdminActivityView,
    MCPAdminHealthView,
    MCPAdminSelfTestView,
    MCPAdminStatDetailView,
    MCPAdminToolDetailView,
    MCPAdminToolsView,
)

app_name = "mcp_admin"

urlpatterns = [
    path("", MCPAdminHealthView.as_view(), name="health"),
    path("health/", MCPAdminHealthView.as_view(), name="health_alias"),
    path("health/stats/<str:stat_type>/", MCPAdminStatDetailView.as_view(), name="stat_detail"),
    path("tools/", MCPAdminToolsView.as_view(), name="tools"),
    path("tools/<str:name>/", MCPAdminToolDetailView.as_view(), name="tool_detail"),
    path("activity/", MCPAdminActivityView.as_view(), name="activity"),
    # POST-only endpoint backing the "Run self-test now" button on Health.
    path("self-test/", MCPAdminSelfTestView.as_view(), name="self_test"),
]
