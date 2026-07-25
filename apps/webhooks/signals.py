"""Outbound event source — a global post_save/post_delete observer.

Mirrors apps/search/signals.py: listens to *every* model's save/delete with no
``sender=`` filter, does an O(1) CRUDView-registry lookup, and ignores any model
whose view didn't opt in with ``enable_webhooks = True``. This is the only tap
point that sees creates, updates, deletes AND raw ORM writes across every surface
(HTML / REST / MCP / sc). Handlers swallow exceptions so a webhook can never break
a model save.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

if TYPE_CHECKING:
    from django.db.models import Model

    from apps.smallstack.crud import CRUDView

logger = logging.getLogger("smallstack.webhooks")


def _view_for(sender: type[Model]) -> type[CRUDView] | None:
    """Return the CRUDView for a model iff it opted into webhooks, else None."""
    from apps.smallstack.crud import CRUDView

    view = CRUDView._registry.get(sender)
    if view is None or not getattr(view, "enable_webhooks", False):
        return None
    return view


def _event_type(sender: type[Model], action: str) -> str:
    """"<app_label>.<model_name>.<action>", e.g. scheduler.scheduledjob.created."""
    meta = sender._meta
    return f"{meta.app_label}.{meta.model_name}.{action}"


def _wants(view: type[CRUDView], action: str) -> bool:
    events = getattr(view, "webhook_events", None)
    return events is None or action in events


def _build_payload(
    view: type[CRUDView], instance: Model, action: str, event_type: str
) -> dict[str, Any]:
    """Event envelope. On create/update the record is serialized with the same
    helper the REST API uses; on delete only the pk survives."""
    from django.utils import timezone

    data: dict[str, Any] = {}
    if action != "deleted":
        try:
            from apps.smallstack.api import serialize

            fields = view._get_detail_fields() if hasattr(view, "_get_detail_fields") else (view.fields or [])
            data = serialize(
                instance,
                list(fields or []),
                extra_fields=list(getattr(view, "api_extra_fields", []) or []),
                expand_fields=set(getattr(view, "api_expand_fields", []) or []),
            )
        except Exception:  # noqa: BLE001 — fall back to a minimal envelope
            data = {"id": instance.pk}
    else:
        data = {"id": instance.pk}

    return {
        "event": event_type,
        "action": action,
        "model": f"{instance._meta.app_label}.{instance._meta.model_name}",
        "id": instance.pk,
        "occurred_at": timezone.now().isoformat(),
        "data": data,
    }


def _fire(sender: type[Model], instance: Model, action: str) -> None:
    view = _view_for(sender)
    if view is None or not _wants(view, action):
        return
    try:
        from . import services

        event_type = _event_type(sender, action)
        payload = _build_payload(view, instance, action, event_type)
        services.fan_out(event_type, payload)
    except Exception:  # noqa: BLE001 — a webhook must never break the save/delete
        logger.exception("webhooks: failed to fire %s for %s", action, sender)


@receiver(post_save)
def _on_save(sender: type[Model], instance: Model, created: bool, **kwargs: Any) -> None:
    _fire(sender, instance, "created" if created else "updated")


@receiver(post_delete)
def _on_delete(sender: type[Model], instance: Model, **kwargs: Any) -> None:
    _fire(sender, instance, "deleted")
