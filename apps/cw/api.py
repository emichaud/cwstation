"""Custom API endpoints for the CW app."""
from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest

from apps.smallstack.api import api_error, api_view

from .consumers import live_group_name

# A batch is a fraction of a second of decode output; anything huge is not ours.
MAX_BATCH_BYTES = 256 * 1024


@api_view(methods=["POST"], require_auth=True)
def live_ingest(request: HttpRequest) -> dict[str, Any] | Any:
    """Relay a live-decode batch from the capture process to the operator's
    open live-view tabs. Authenticated via Bearer token (the capture command
    mints a short-lived one) or session; the group is derived from the
    authenticated user, so a token can only feed its own operator's tape."""
    payload = request.json
    if not isinstance(payload, dict):
        return api_error("Expected a JSON object", 400)
    if len(request.body) > MAX_BATCH_BYTES:
        return api_error("Batch too large", 413)

    layer = get_channel_layer()
    if layer is None:
        return api_error("Channel layer not configured", 503)
    async_to_sync(layer.group_send)(
        live_group_name(request.user.pk),
        {"type": "cw.batch", "payload": payload},
    )
    return {"relayed": True}
