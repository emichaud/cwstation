"""WebSocket consumer for the live CW tape.

Each authenticated user gets their own group (`cw-live-<pk>`): the ingest
endpoint relays batches from the capture process into the group, and every
open live-view tab receives them. The consumer itself is a pure relay — it
never inspects the payload (the event contract lives in the engine).
"""
from __future__ import annotations

from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer


def live_group_name(user_pk: int) -> str:
    return f"cw-live-{user_pk}"


class LiveTapeConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close()
            return
        self.group = live_group_name(user.pk)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def cw_batch(self, event: dict[str, Any]) -> None:
        """Handler for `{"type": "cw.batch", "payload": {...}}` group messages."""
        await self.send_json(event["payload"])
