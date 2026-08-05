"""SmallStack webhooks — a solid, extensible outbound + inbound event surface.

Public API (import from ``apps.webhooks``):

* :func:`suppress_webhooks` — context manager: writes inside it emit no outbound events
  (the loop guard). Use it around an inbound handler's write-back, an import, a backfill.
* The four extension seams — :func:`webhook_transform`, :func:`webhook_auth`,
  :func:`webhook_verifier`, :func:`webhook_challenge` — register plug-ins that reshape
  outbound payloads, add outbound auth, verify inbound signatures, and answer inbound
  handshakes. Each has a built-in default so core behavior is unchanged.
* :func:`webhook_handler` — register the function that processes an inbound receiver's POST.

Everything else (models, services, tasks) is internal.
"""

from __future__ import annotations

from .context import current_origin, suppress_webhooks, suppressed
from .hooks import (
    AuthResult,
    OutgoingRequest,
    Transformed,
    webhook_auth,
    webhook_challenge,
    webhook_transform,
    webhook_verifier,
)
from .registry import webhook_handler

__all__ = [
    "AuthResult",
    "OutgoingRequest",
    "Transformed",
    "current_origin",
    "suppress_webhooks",
    "suppressed",
    "webhook_auth",
    "webhook_challenge",
    "webhook_handler",
    "webhook_transform",
    "webhook_verifier",
]
