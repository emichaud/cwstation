from __future__ import annotations

from django.urls import path

from .views import CWSessionCRUDView, DecodeView, MonitorView, SendView, session_audio

urlpatterns = [
    path("cw/", MonitorView.as_view(), name="cw-monitor"),
    path("cw/decode/", DecodeView.as_view(), name="cw-decode"),
    path("cw/send/", SendView.as_view(), name="cw-send"),
    path("cw/sessions/<int:pk>/audio.wav", session_audio, name="cw-session-audio"),
    *CWSessionCRUDView.get_urls(),
]
