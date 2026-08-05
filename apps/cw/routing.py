from __future__ import annotations

from django.urls import path

from .consumers import LiveTapeConsumer

websocket_urlpatterns = [
    path("ws/cw/live/", LiveTapeConsumer.as_asgi()),
]
