"""Inbound handler registry + the ``@webhook_handler`` decorator.

Self-registering, same shape as the scheduler's ``@scheduled`` and MCP's
``@tool``: decorate a function with a slug and it's discoverable at dispatch time.
Handlers live in each app's ``webhook_handlers.py`` (autodiscovered by the same
helper the CRUDView/MCP registries use).

    @webhook_handler("stripe-events")
    def on_stripe(receipt):
        payload = receipt.json()      # parsed body (or None)
        ...                           # create/update records via normal ORM

The handler runs inside ``dispatch_incoming`` (a background task), so it may do
real work; raising marks the WebhookReceipt FAILED without crashing the worker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .models import WebhookReceipt

logger = logging.getLogger("smallstack.webhooks")

# An inbound handler takes the persisted WebhookReceipt and returns nothing.
Handler = Callable[["WebhookReceipt"], None]

_HANDLERS: dict[str, Handler] = {}


def webhook_handler(name: str) -> Callable[[Handler], Handler]:
    """Register ``fn`` as the handler for inbound receiver ``name`` (its slug).

    First-wins on duplicate names (a warning is logged) so autodiscovery can run
    more than once safely.
    """

    def decorator(fn: Handler) -> Handler:
        if name in _HANDLERS:
            logger.warning("webhook handler %r already registered — keeping the first", name)
            return fn
        _HANDLERS[name] = fn
        logger.debug("webhooks: registered inbound handler %r", name)
        return fn

    return decorator


def get_handler(name: str) -> Handler | None:
    return _HANDLERS.get(name)


def registered_handlers() -> list[str]:
    return sorted(_HANDLERS)


def clear_handlers_for_tests() -> None:
    """Test helper — wipe the registry between tests."""
    _HANDLERS.clear()
