"""Django admin for webhook models. The primary UI is the themed CRUDView pages;
these register the models in /admin/ for low-level inspection."""

from __future__ import annotations

from django.contrib import admin

from .models import WebhookDelivery, WebhookEndpoint, WebhookReceipt, WebhookReceiver


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("name", "target_url", "enabled", "last_status", "total_deliveries", "consecutive_failures")
    list_filter = ("enabled", "last_status")
    search_fields = ("name", "target_url")
    readonly_fields = ("secret", "last_delivery_at", "last_status", "total_deliveries", "consecutive_failures")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("event_type", "endpoint", "status", "attempt", "response_status", "created_at")
    list_filter = ("status",)
    search_fields = ("event_type",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WebhookReceiver)
class WebhookReceiverAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "handler", "enabled", "total_received")
    list_filter = ("enabled", "require_signature")
    search_fields = ("name", "slug")
    readonly_fields = ("secret", "last_received_at", "total_received")


@admin.register(WebhookReceipt)
class WebhookReceiptAdmin(admin.ModelAdmin):
    list_display = ("receiver", "status", "verified", "source_ip", "received_at")
    list_filter = ("status", "verified")
    readonly_fields = ("received_at",)
