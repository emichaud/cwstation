"""One real HTTP round-trip — proves the actual urllib POST + signature path works
(the other delivery tests mock urlopen). A throwaway localhost HTTP server captures
the request; the delivery target points at it with ALLOW_PRIVATE on.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from apps.webhooks import services
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
from apps.webhooks.tasks import deliver_webhook

pytestmark = pytest.mark.django_db

_CAPTURED: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _CAPTURED["body"] = self.rfile.read(length)
        _CAPTURED["signature"] = self.headers.get("X-SmallStack-Signature")
        _CAPTURED["event"] = self.headers.get("X-SmallStack-Event")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence the server's stderr logging
        pass


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address  # (host, port)
    finally:
        server.shutdown()
        _CAPTURED.clear()


def test_real_http_delivery_with_valid_signature(http_server, settings):
    settings.SMALLSTACK_WEBHOOK_ALLOW_PRIVATE = True
    host, port = http_server
    ep = WebhookEndpoint.objects.create(
        name="local",
        target_url=f"http://{host}:{port}/hook",
        secret="topsecret",
        event_filter=["*"],
    )
    delivery = WebhookDelivery.objects.create(
        endpoint=ep, event_type="t.t.created", payload={"hello": "world"}, max_attempts=1
    )

    result = deliver_webhook.func(delivery.pk)

    assert result["success"] is True
    delivery.refresh_from_db()
    assert delivery.status == WebhookDelivery.Status.SUCCESS
    assert delivery.response_status == 200
    # The receiver got the body and a signature that verifies under the secret.
    assert _CAPTURED["event"] == "t.t.created"
    assert services.verify("topsecret", _CAPTURED["body"], _CAPTURED["signature"])
