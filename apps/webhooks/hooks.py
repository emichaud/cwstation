"""The four extension seams — named-registry hooks the foundation is built on.

SmallStack ships a *solid* webhook engine and four documented seams so a Zapier / n8n /
Azure / AWS integration is a small plug-in, not a core fork. Each seam is a named
registry, exactly like ``@webhook_handler`` / ``mcp_tools.py``: decorate a function with a
name, drop it in an app's ``webhook_*.py``, and it's discovered at ``ready()``. Every seam
ships a **built-in default** (registered here) so with no selection the engine behaves
exactly as it did before this change.

Selected per endpoint / per receiver by name:

===================  ==================================  =========================
Seam                 Model field                         Default
===================  ==================================  =========================
``@webhook_transform``  ``WebhookEndpoint.transform``    ``"smallstack"``  (current envelope)
``@webhook_auth``       ``WebhookEndpoint.auth_scheme``  ``"hmac"``        (X-SmallStack-Signature)
``@webhook_verifier``   ``WebhookReceiver.verifier``     ``"hmac"``        (raw-body HMAC)
``@webhook_challenge``  ``WebhookReceiver.challenge``    ``""``            (none)
===================  ==================================  =========================

An unknown selector name falls back to the default with a logged warning — a typo must
not silently drop a delivery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from .models import WebhookEndpoint, WebhookReceiver

logger = logging.getLogger("smallstack.webhooks")


# ---------------------------------------------------------------------------
# Value objects passed to / returned from the seams
# ---------------------------------------------------------------------------


@dataclass
class Transformed:
    """Result of an outbound transform: the wire body + its content type."""

    body: bytes
    content_type: str = "application/json"


@dataclass
class OutgoingRequest:
    """The request an auth seam signs — mutable view the seam reads to compute a
    credential (it returns headers/params via :class:`AuthResult`, it does not mutate
    this in place)."""

    url: str
    body: bytes
    headers: dict[str, str]
    event_type: str


@dataclass
class AuthResult:
    """Credentials an auth seam adds to the outgoing request."""

    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)


# A transform takes the event envelope dict and returns a Transformed.
Transform = Callable[[dict[str, Any]], "Transformed"]
# An auth seam takes the OutgoingRequest + endpoint and returns credentials.
Auth = Callable[["OutgoingRequest", "WebhookEndpoint"], "AuthResult"]
# A verifier takes raw body + headers + receiver and returns bool (constant-time inside).
Verifier = Callable[[bytes, "dict[str, str]", "WebhookReceiver"], bool]
# A challenge takes the request and returns a response to short-circuit, or None.
Challenge = Callable[["HttpRequest", "WebhookReceiver"], "HttpResponse | None"]


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_TRANSFORMS: dict[str, Transform] = {}
_AUTHS: dict[str, Auth] = {}
_VERIFIERS: dict[str, Verifier] = {}
_CHALLENGES: dict[str, Challenge] = {}


def _register(registry: dict[str, Any], kind: str, name: str) -> Callable[[Any], Any]:
    def decorator(fn: Any) -> Any:
        if name in registry:
            logger.warning("webhook %s %r already registered — keeping the first", kind, name)
            return fn
        registry[name] = fn
        logger.debug("webhooks: registered %s %r", kind, name)
        return fn

    return decorator


def webhook_transform(name: str) -> Callable[[Transform], Transform]:
    """Register an outbound payload transform under ``name`` (``endpoint.transform``)."""
    return _register(_TRANSFORMS, "transform", name)


def webhook_auth(name: str) -> Callable[[Auth], Auth]:
    """Register an outbound per-request auth scheme under ``name`` (``endpoint.auth_scheme``)."""
    return _register(_AUTHS, "auth", name)


def webhook_verifier(name: str) -> Callable[[Verifier], Verifier]:
    """Register an inbound signature verifier under ``name`` (``receiver.verifier``)."""
    return _register(_VERIFIERS, "verifier", name)


def webhook_challenge(name: str) -> Callable[[Challenge], Challenge]:
    """Register an inbound challenge/handshake under ``name`` (``receiver.challenge``)."""
    return _register(_CHALLENGES, "challenge", name)


# ---------------------------------------------------------------------------
# Lookups — resolve a selector to a callable, falling back to the default
# ---------------------------------------------------------------------------


def get_transform(name: str | None) -> Transform:
    return _resolve(_TRANSFORMS, name or DEFAULT_TRANSFORM, DEFAULT_TRANSFORM, "transform")


def get_auth(name: str | None) -> Auth:
    return _resolve(_AUTHS, name or DEFAULT_AUTH, DEFAULT_AUTH, "auth")


def get_verifier(name: str | None) -> Verifier:
    return _resolve(_VERIFIERS, name or DEFAULT_VERIFIER, DEFAULT_VERIFIER, "verifier")


def get_challenge(name: str | None) -> Challenge | None:
    """Challenge is opt-in: blank ⇒ no handshake (returns None)."""
    if not name:
        return None
    fn = _CHALLENGES.get(name)
    if fn is None:
        logger.warning("webhooks: unknown challenge %r — no handshake will run", name)
    return fn


def _resolve(registry: dict[str, Any], name: str, default: str, kind: str) -> Any:
    fn = registry.get(name)
    if fn is not None:
        return fn
    if name != default:
        logger.warning("webhooks: unknown %s %r — falling back to %r", kind, name, default)
    return registry[default]


def registered() -> dict[str, list[str]]:
    """All registered seam names by kind (for the doctor / --explain)."""
    return {
        "transforms": sorted(_TRANSFORMS),
        "auths": sorted(_AUTHS),
        "verifiers": sorted(_VERIFIERS),
        "challenges": sorted(_CHALLENGES),
    }


def clear_hooks_for_tests() -> None:
    """Test helper — wipe custom hooks and re-register the built-in defaults."""
    _TRANSFORMS.clear()
    _AUTHS.clear()
    _VERIFIERS.clear()
    _CHALLENGES.clear()
    register_default_hooks()


# ---------------------------------------------------------------------------
# Built-in defaults — reproduce today's behavior exactly
# ---------------------------------------------------------------------------

DEFAULT_TRANSFORM = "smallstack"
DEFAULT_AUTH = "hmac"
DEFAULT_VERIFIER = "hmac"


def _default_transform(event: dict[str, Any]) -> Transformed:
    """The current SmallStack envelope, JSON-encoded (identity transform)."""
    import json

    return Transformed(
        body=json.dumps(event, default=str).encode(),
        content_type="application/json",
    )


def _default_auth(req: OutgoingRequest, endpoint: WebhookEndpoint) -> AuthResult:
    """The current HMAC signature header (``X-SmallStack-Signature: sha256=…``)."""
    from . import services

    return AuthResult(
        headers={services.SIGNATURE_HEADER: services.signature_header_value(endpoint.secret, req.body)}
    )


def _default_verifier(body: bytes, headers: dict[str, str], receiver: WebhookReceiver) -> bool:
    """The current raw-body HMAC check against ``signature_header`` (GitHub-compatible)."""
    from . import services

    provided = headers.get(receiver.signature_header, "")
    return services.verify(receiver.secret, body, provided)


def register_default_hooks() -> None:
    """Register the built-in defaults if absent. Idempotent and quiet, so safe to call at
    import time and again from ``ready()`` / the test suite."""
    if DEFAULT_TRANSFORM not in _TRANSFORMS:
        webhook_transform(DEFAULT_TRANSFORM)(_default_transform)
    if DEFAULT_AUTH not in _AUTHS:
        webhook_auth(DEFAULT_AUTH)(_default_auth)
    if DEFAULT_VERIFIER not in _VERIFIERS:
        webhook_verifier(DEFAULT_VERIFIER)(_default_verifier)


register_default_hooks()
