"""Webhook URLs (staff surfaces).

Mounted at the smallstack root with a ``webhooks/`` path prefix. No ``app_name``
— bare URL names keep CRUDView's internal reverses working (same footgun the
scheduler documents). The PUBLIC inbound receiver + the localhost tick are mounted
separately in config/urls.py (they must not sit behind /smallstack/ auth).
"""

from django.urls import path

from .views import (
    WebhookDeliveryCRUDView,
    WebhookEndpointCRUDView,
    WebhookReceiptCRUDView,
    WebhookReceiverCRUDView,
    WebhooksDashboardView,
    replay_dead_deliveries,
    replay_delivery,
    reveal_receiver_secret,
    reveal_secret,
    rotate_receiver_secret,
    rotate_secret,
    test_endpoint,
)

urlpatterns = [
    path("webhooks/", WebhooksDashboardView.as_view(), name="webhooks_dashboard"),
    path("webhooks/endpoints/<int:pk>/test/", test_endpoint, name="webhooks_test_endpoint"),
    path("webhooks/endpoints/<int:pk>/reveal/", reveal_secret, name="webhooks_reveal_secret"),
    path("webhooks/endpoints/<int:pk>/rotate/", rotate_secret, name="webhooks_rotate_secret"),
    path("webhooks/deliveries/<int:pk>/replay/", replay_delivery, name="webhooks_replay_delivery"),
    path("webhooks/deliveries/replay-dead/", replay_dead_deliveries, name="webhooks_replay_dead"),
    path("webhooks/receivers/<int:pk>/reveal/", reveal_receiver_secret, name="webhooks_reveal_receiver_secret"),
    path("webhooks/receivers/<int:pk>/rotate/", rotate_receiver_secret, name="webhooks_rotate_receiver_secret"),
    *WebhookEndpointCRUDView.get_urls(),
    *WebhookDeliveryCRUDView.get_urls(),
    *WebhookReceiverCRUDView.get_urls(),
    *WebhookReceiptCRUDView.get_urls(),
]
