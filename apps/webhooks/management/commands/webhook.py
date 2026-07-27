"""``webhook`` — operational CLI for the webhook surface.

The generic ``sc`` verbs already cover CRUD on endpoints/receivers
(``sc ls/get/new/set/rm webhook``); this command adds the *operational* verbs
that CRUD can't express — a status report, a test send, a replay, a delivery-log
view, and a manual retry tick. Fronted by ``sc webhook <subcommand>`` too.

    python manage.py webhook status
    python manage.py webhook list
    python manage.py webhook test "Zapier"
    python manage.py webhook deliveries --status dead --limit 20
    python manage.py webhook replay 42
    python manage.py webhook tick
"""

from __future__ import annotations

import argparse
import json as jsonlib
from typing import Any, Callable

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.webhooks import services
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint, WebhookReceiver

SUBCOMMANDS = ("status", "list", "test", "replay", "deliveries", "tick")


class Command(BaseCommand):
    help = "Operational CLI for webhooks: status | list | test | replay | deliveries | tick."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("subcommand", nargs="?", help=" | ".join(SUBCOMMANDS))
        parser.add_argument("target", nargs="?", help="Endpoint (id/name) or delivery id, per subcommand.")
        parser.add_argument("--status", help="Filter deliveries by status (or 'replay --status dead' for bulk).")
        parser.add_argument("--endpoint", help="Endpoint (id/name) filter for bulk replay.")
        parser.add_argument("--since", help="ISO timestamp: only replay dead deliveries created after this.")
        parser.add_argument("--limit", type=int, default=20, help="Row cap for deliveries / bulk replay.")
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args: Any, **options: Any) -> None:
        sub = options.get("subcommand")
        if sub is None:
            self.stdout.write(self.help)
            self.stdout.write("\nSubcommands: " + ", ".join(SUBCOMMANDS))
            return
        if sub not in SUBCOMMANDS:
            raise CommandError(f"unknown subcommand {sub!r}; use one of: {', '.join(SUBCOMMANDS)}")
        getattr(self, f"_{sub}")(options)

    # -- helpers ------------------------------------------------------------

    def _emit(self, data: Any, as_json: bool, render: Callable[[Any], None]) -> None:
        if as_json:
            self.stdout.write(jsonlib.dumps(data, indent=2, default=str))
        else:
            render(data)

    def _resolve_endpoint(self, target: str | None) -> WebhookEndpoint:
        if not target:
            raise CommandError("this subcommand needs an endpoint (id or name).")
        ep = None
        if target.isdigit():
            ep = WebhookEndpoint.objects.filter(pk=int(target)).first()
        if ep is None:
            ep = WebhookEndpoint.objects.filter(name=target).first()
        if ep is None:
            raise CommandError(f"no endpoint matching {target!r} (by id or exact name).")
        return ep

    # -- subcommands --------------------------------------------------------

    def _status(self, options: dict[str, Any]) -> None:
        by_status = dict(
            WebhookDelivery.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
        )
        data = {
            "endpoints": {
                "active": WebhookEndpoint.objects.filter(enabled=True).count(),
                "total": WebhookEndpoint.objects.count(),
            },
            "receivers": {
                "active": WebhookReceiver.objects.filter(enabled=True).count(),
                "total": WebhookReceiver.objects.count(),
            },
            "deliveries": by_status,
            "retry_backlog": WebhookDelivery.objects.filter(
                status=WebhookDelivery.Status.RETRYING
            ).count(),
        }

        def render(d: dict[str, Any]) -> None:
            self.stdout.write("Webhooks status")
            self.stdout.write(f"  endpoints : {d['endpoints']['active']} active / {d['endpoints']['total']} total")
            self.stdout.write(f"  receivers : {d['receivers']['active']} active / {d['receivers']['total']} total")
            self.stdout.write("  deliveries:")
            for status in ("pending", "retrying", "success", "failed", "dead"):
                self.stdout.write(f"      {status:9s} {d['deliveries'].get(status, 0)}")
            self.stdout.write(f"  retry backlog: {d['retry_backlog']}")

        self._emit(data, options["json"], render)

    def _list(self, options: dict[str, Any]) -> None:
        rows = [
            {
                "id": e.pk,
                "name": e.name,
                "target_url": e.target_url,
                "enabled": e.enabled,
                "last_status": e.last_status or "",
                "total_deliveries": e.total_deliveries,
                "consecutive_failures": e.consecutive_failures,
            }
            for e in WebhookEndpoint.objects.all()
        ]

        def render(data: list[dict[str, Any]]) -> None:
            if not data:
                self.stdout.write("No endpoints registered.")
                return
            for r in data:
                flag = "on " if r["enabled"] else "off"
                self.stdout.write(
                    f"  #{r['id']:<4} [{flag}] {r['name']:<24} {r['last_status'] or '—':<9} "
                    f"{r['total_deliveries']} sent, {r['consecutive_failures']} fail-streak  {r['target_url']}"
                )

        self._emit(rows, options["json"], render)

    def _disabled_note(self, endpoint: WebhookEndpoint) -> str:
        return (
            f'note: endpoint "{endpoint.name}" is disabled — signal events will not '
            f"deliver (sc set webhook {endpoint.pk} --enabled=true)"
        )

    def _test(self, options: dict[str, Any]) -> None:
        from django.utils import timezone

        ep = self._resolve_endpoint(options.get("target"))
        delivery = WebhookDelivery.objects.create(
            endpoint=ep,
            event_type="webhooks.test.ping",
            payload={
                "event": "webhooks.test.ping",
                "action": "test",
                "occurred_at": timezone.now().isoformat(),
                "data": {"message": "Test delivery from the webhook CLI."},
            },
            max_attempts=1,  # tests don't retry: a failure goes straight to dead
        )
        services._enqueue_delivery(delivery.pk)
        data = {"queued": True, "delivery_id": delivery.pk, "endpoint": ep.name,
                "endpoint_enabled": ep.enabled}

        def render(d: dict[str, Any]) -> None:
            self.stdout.write(f"Queued test delivery #{d['delivery_id']} to “{d['endpoint']}”.")
            if not d["endpoint_enabled"]:
                self.stdout.write(self._disabled_note(ep))

        self._emit(data, options["json"], render)

    def _replay(self, options: dict[str, Any]) -> None:
        # Bulk dead-letter replay (F-023): `sc webhook replay --status dead [...]`.
        if options.get("status"):
            self._replay_bulk(options)
            return
        target = options.get("target")
        if not target or not target.isdigit():
            raise CommandError("replay needs a delivery id, or --status dead for bulk.")
        original = WebhookDelivery.objects.filter(pk=int(target)).select_related("endpoint").first()
        if original is None:
            raise CommandError(f"no delivery #{target}.")
        replay = services.replay_delivery(original)
        data = {"queued": True, "delivery_id": replay.pk, "replayed_from": original.pk,
                "endpoint_enabled": original.endpoint.enabled}

        def render(d: dict[str, Any]) -> None:
            self.stdout.write(f"Replayed delivery #{d['replayed_from']} as #{d['delivery_id']}.")
            if not d["endpoint_enabled"]:
                self.stdout.write(self._disabled_note(original.endpoint))

        self._emit(data, options["json"], render)

    def _replay_bulk(self, options: dict[str, Any]) -> None:
        if options["status"] != "dead":
            raise CommandError("bulk replay only supports --status dead.")
        endpoint_id = None
        if options.get("endpoint"):
            endpoint_id = self._resolve_endpoint(options["endpoint"]).pk
        since = None
        if options.get("since"):
            from django.utils.dateparse import parse_datetime

            since = parse_datetime(options["since"])
            if since is None:
                raise CommandError(f"could not parse --since {options['since']!r} (use ISO 8601).")
        new_ids = services.replay_dead(
            endpoint_id=endpoint_id, since=since, limit=options["limit"]
        )
        data = {"queued": len(new_ids), "delivery_ids": new_ids}

        def render(d: dict[str, Any]) -> None:
            self.stdout.write(f"Replayed {d['queued']} dead delivery(ies): {d['delivery_ids'] or '—'}")

        self._emit(data, options["json"], render)

    def _deliveries(self, options: dict[str, Any]) -> None:
        qs = WebhookDelivery.objects.select_related("endpoint").order_by("-created_at")
        if options.get("target"):
            qs = qs.filter(endpoint=self._resolve_endpoint(options["target"]))
        if options.get("status"):
            qs = qs.filter(status=options["status"])
        qs = qs[: options["limit"]]
        rows = [
            {
                "id": d.pk,
                "endpoint": d.endpoint.name,
                "event_type": d.event_type,
                "status": d.status,
                "attempt": d.attempt,
                "response_status": d.response_status,
                "created_at": d.created_at.isoformat(),
            }
            for d in qs
        ]

        def render(data: list[dict[str, Any]]) -> None:
            if not data:
                self.stdout.write("No deliveries match.")
                return
            for r in data:
                code = r["response_status"] or "—"
                self.stdout.write(
                    f"  #{r['id']:<5} {r['status']:<9} {r['event_type']:<28} "
                    f"→ {r['endpoint']:<20} HTTP {code} (try {r['attempt']})  {r['created_at']}"
                )

        self._emit(rows, options["json"], render)

    def _tick(self, options: dict[str, Any]) -> None:
        claimed = services.run_due_deliveries()
        self._emit(
            {"claimed": claimed},
            options["json"],
            lambda d: self.stdout.write(f"Claimed {d['claimed']} delivery(ies) for retry."),
        )
