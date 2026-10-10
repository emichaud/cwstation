"""SmallStack implements MCP itself — it must not depend on the `mcp` SDK.

The SDK was a declared runtime dependency for one thing: a singleton
`mcp.server.lowlevel.Server` in `apps/mcp/server.py` that **nothing ever
read**. The protocol — JSON-RPC envelope, initialize / tools-list / tools-call
dispatch, OAuth — is plain Django in `apps/mcp/views.py`, served by gunicorn.

Dropping it removed pydantic, starlette, uvicorn, sse-starlette, httpx2,
jsonschema, pyjwt[crypto], opentelemetry-api and python-multipart from every
install, several of which are ASGI machinery SmallStack never runs.

This guard exists because the regression is invisible: a stray
`from mcp...` import would work fine in any dev environment that still has the
package lying around in its virtualenv, and would only fail for a downstream
doing a clean install.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APPS = REPO_ROOT / "apps"
CONFIG = REPO_ROOT / "config"


def _imported_top_level_modules(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not our source
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import — never the SDK.
            if not node.level and node.module:
                names.add(node.module.split(".")[0])
    return names


def _source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in (APPS, CONFIG):
        files.extend(p for p in root.rglob("*.py") if "migrations" not in p.parts)
    return files


def test_no_module_imports_the_mcp_sdk():
    """`mcp` the PyPI package — not `apps.mcp`, which is ours."""
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _source_files()
        if "mcp" in _imported_top_level_modules(p)
    ]
    assert not offenders, (
        "these modules import the `mcp` SDK, which SmallStack no longer depends on "
        "(it implements the protocol itself): " + ", ".join(offenders)
    )


def test_mcp_sdk_is_not_a_declared_dependency():
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    declared = pyproject["project"]["dependencies"]
    names = {
        d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in declared
    }
    assert "mcp" not in names, f"`mcp` is declared again in pyproject: {declared}"


@pytest.mark.django_db
def test_the_mcp_surface_still_works_without_the_sdk():
    """The point of the removal: nothing about the protocol depended on it."""
    import json

    from django.test import Client

    from apps.mcp.server import TOOL_REGISTRY

    resp = Client().post(
        "/mcp",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        ),
        content_type="application/json",
        HTTP_HOST="localhost",
    )
    # initialize is reachable (auth is asserted elsewhere); the registry is ours.
    assert resp.status_code in (200, 401)
    assert isinstance(TOOL_REGISTRY, dict)
