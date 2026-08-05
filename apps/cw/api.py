"""Custom API endpoints for the CW app."""
from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest

from apps.smallstack.api import api_error, api_view

from .consumers import live_group_name
from .models import CWSimControl

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


_CONTROL_FIELDS = ("noise_level", "input_gain", "squelch_db", "afc", "paused_signals")


@api_view(methods=["GET", "POST"], require_auth=True)
def sim_control(request: HttpRequest) -> dict[str, Any] | Any:
    """Read (GET) or update (POST, partial) the operator's live sim knobs.
    The running `cw_simulate` process polls this row and applies changes."""
    control, _ = CWSimControl.objects.get_or_create(user=request.user)
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        for name in _CONTROL_FIELDS:
            if name in data:
                value = data[name]
                if not isinstance(value, (int, float, bool)):
                    return api_error(f"{name} must be a number or boolean", 400)
                setattr(control, name, value)
        control.clamped().save()
    return {name: getattr(control, name) for name in _CONTROL_FIELDS}
