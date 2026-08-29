"""Shared fixtures for the telemetry test suite."""

import pytest


@pytest.fixture(autouse=True)
def _ensure_telemetry_mcp_tools_registered():
    """Re-register the ``logs_*`` MCP tools before each test.

    The MCP test suite's fixtures call ``clear_registry_for_tests()``, which
    wipes the shared ``TOOL_REGISTRY`` / ``TOOL_HANDLERS`` — including tools
    registered at import time. Re-importing a cached module does not
    re-register, so without this these tests pass alone and fail after an MCP
    test, which is the worst kind of failure to debug. Same accommodation
    apps/runbook/tests/conftest.py makes, for the same reason.
    """
    from apps.telemetry.mcp_tools import register_telemetry_tools

    register_telemetry_tools()
