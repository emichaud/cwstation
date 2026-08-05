"""URL config for apps/mcp.

McpHttpView is mounted at BOTH /mcp AND /mcp/ — this is non-negotiable.
Django's APPEND_SLASH would otherwise 301 a POST → GET on /mcp, breaking
JSON-RPC silently. Several MCP clients (including Claude.ai) send POST to
the no-slash variant.

oauth_wellknown_urlpatterns is exported separately so config/urls.py can
mount the RFC 8414 / RFC 9728 discovery paths at the root.
"""

from django.urls import path

from .oauth_views import (
    AuthorizeView,
    authorization_server_metadata,
    protected_resource_metadata,
    register,
    revoke,
    token,
)
from .views import McpHttpView

app_name = "mcp"


_rpc = McpHttpView.as_view()

urlpatterns = [
    # Both /mcp and /mcp/ point at the same view. The non-trailing variant
    # is NOT redundant — see module docstring.
    path("mcp", _rpc, name="rpc_no_slash"),
    path("mcp/", _rpc, name="rpc"),
    # OAuth surface (Claude.ai Connectors compatible)
    path("mcp/oauth/register", register, name="oauth_register"),
    path("mcp/oauth/authorize", AuthorizeView.as_view(), name="authorize"),
    path("mcp/oauth/token", token, name="oauth_token"),
    path("mcp/oauth/revoke", revoke, name="oauth_revoke"),
]


# Well-known endpoints live at the root, not under /mcp/, so RFC 8414 /
# RFC 9728 discovery works as clients expect. Both bare and path-suffixed
# variants are registered — some clients (Claude.ai is one) probe both.
oauth_wellknown_urlpatterns = [
    path(
        ".well-known/oauth-authorization-server",
        authorization_server_metadata,
        name="oauth_as_metadata",
    ),
    path(
        ".well-known/oauth-authorization-server/mcp",
        authorization_server_metadata,
        name="oauth_as_metadata_mcp",
    ),
    path(
        ".well-known/oauth-protected-resource",
        protected_resource_metadata,
        name="oauth_prm_metadata",
    ),
    path(
        ".well-known/oauth-protected-resource/mcp",
        protected_resource_metadata,
        name="oauth_prm_metadata_mcp",
    ),
]
