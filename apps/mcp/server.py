"""MCP server registry: singleton Server, @tool decorator, per-request ToolContext.

The HTTP layer (views.py) reads TOOL_REGISTRY for tools/list and dispatches
through TOOL_HANDLERS for tools/call. Tool callbacks read `current_context()`
to get the authenticated user/token, set by views.py before invoking.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from django.conf import settings

logger = logging.getLogger("smallstack.mcp.server")


# ---------------------------------------------------------------------------
# Singleton MCP server (mcp SDK low-level Server)
# ---------------------------------------------------------------------------

from mcp.server.lowlevel import Server as _Server  # noqa: E402

mcp_server: _Server = _Server(getattr(settings, "MCP_SERVER_NAME", "smallstack"))


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """Tool metadata. The handler is stored separately in TOOL_HANDLERS."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    write: bool = False
    requires_access: Optional[str] = None  # "readonly" | "staff" | "auth" | None
    # Optional callable applied at tools/list time. ``(user) -> bool``.
    # When set and it returns False, the tool is hidden from that user's
    # tools/list response. Useful for tools where the access decision
    # isn't expressible as a flat ``requires_access`` level — the search
    # MCP tools (round-2 audit §3.3) gate by the underlying view's
    # ``search_access`` tier, which check_tool_access can't see. Without
    # this hook the tool was visible-but-non-functional to non-staff
    # callers — they saw ``search_users`` in the list and got
    # ``{"denied": true}`` on call. Returning False here hides it
    # from the list AND tools/call's existing check_tool_access still
    # gates the call itself (defense in depth).
    visible_to: Optional[Callable[[Any], bool]] = None


# Module-level registries — populated by @tool, read by views.py + factory.
TOOL_REGISTRY: dict[str, ToolDef] = {}
TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any] | None = None,
    *,
    write: bool = False,
    requires_access: str | None = None,
    visible_to: Callable[[Any], bool] | None = None,
) -> Callable[[Callable], Callable]:
    """Register an async tool callback with the MCP server.

    Usage:
        @tool("today", "Return today's local date and weekday.")
        async def today(args: dict) -> dict:
            ctx = current_context()
            return {"date": ..., "user": ctx.user.username}

    Idempotent: registering the same name twice logs a warning and keeps
    the first registration (so the factory can be re-run safely).
    """

    def decorator(fn: Callable[[dict[str, Any]], Awaitable[Any]]) -> Callable:
        if name in TOOL_REGISTRY:
            logger.warning("MCP tool %r already registered — skipping duplicate", name)
            return fn
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
            write=write,
            requires_access=requires_access,
            visible_to=visible_to,
        )
        TOOL_HANDLERS[name] = fn
        logger.debug("MCP registered tool %r (write=%s, requires=%s)", name, write, requires_access)
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Per-request context (user + token visible to tool callbacks)
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    user: Any  # AUTH_USER_MODEL instance
    token: Any  # APIToken instance


_context_var: ContextVar[ToolContext | None] = ContextVar("mcp_tool_context", default=None)


def set_context(ctx: ToolContext | None) -> Any:
    """Set the current tool context. Returns a token usable with reset_context."""
    return _context_var.set(ctx)


def reset_context(token: Any) -> None:
    _context_var.reset(token)


def current_context() -> ToolContext:
    """Return the active ToolContext.

    Raises LookupError if called outside a tool dispatch — tool callbacks
    should rely on this; helpers shared across HTTP + tool paths must check
    `try: current_context() except LookupError`.
    """
    ctx = _context_var.get()
    if ctx is None:
        raise LookupError("ToolContext is not set. current_context() called outside a tool dispatch.")
    return ctx


def clear_registry_for_tests() -> None:
    """Test helper: wipe the registries between tests. Do not use in prod."""
    TOOL_REGISTRY.clear()
    TOOL_HANDLERS.clear()
