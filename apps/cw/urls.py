from __future__ import annotations

from django.urls import path

from .api import live_ingest, sim_control
from .views import (
    CWSessionCRUDView,
    DecodeView,
    LiveView,
    MonitorView,
    SendView,
    SimulatorView,
    session_audio,
)

urlpatterns = [
    path("cw/", MonitorView.as_view(), name="cw-monitor"),
    path("cw/live/", LiveView.as_view(), name="cw-live"),
    path("cw/live/ingest/", live_ingest, name="cw-live-ingest"),
    path("cw/sim/", SimulatorView.as_view(), name="cw-sim"),
    path("cw/sim/control/", sim_control, name="cw-sim-control"),
    path("cw/decode/", DecodeView.as_view(), name="cw-decode"),
    path("cw/send/", SendView.as_view(), name="cw-send"),
    path("cw/sessions/<int:pk>/audio.wav", session_audio, name="cw-session-audio"),
    *CWSessionCRUDView.get_urls(),
]
