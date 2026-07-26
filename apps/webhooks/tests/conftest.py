"""Shared fixtures for the webhooks test suite."""

import pytest


@pytest.fixture(autouse=True)
def _ensure_webhook_mcp_tools_registered():
    """Re-register the webhook MCP tools before each test.

    The MCP test suite's ``clean_registry`` fixture calls
    ``clear_registry_for_tests()``, which wipes the shared ``TOOL_HANDLERS`` /
    ``TOOL_REGISTRY`` — including the webhook tools registered at startup
    (both the custom ``summary_deliveries``/``test_webhook``/``replay_delivery``
    tools and the CRUDView factory tools like ``create_webhook``). Without
    this, whether the webhook MCP tests find their tools depends on test
    ordering (they pass alone, fail after an MCP test). Both registration
    paths are idempotent. Mirrors the runbook/search conftest pattern.
    """
    try:
        from apps.mcp.factory import register_mcp_tools_from_crudview
    except ImportError:  # pragma: no cover — MCP app not installed downstream
        return

    from apps.webhooks.mcp_tools import register_webhook_tools
    from apps.webhooks.views import WebhookEndpointCRUDView, WebhookReceiverCRUDView

    register_webhook_tools()
    register_mcp_tools_from_crudview(WebhookEndpointCRUDView)
    register_mcp_tools_from_crudview(WebhookReceiverCRUDView)
