"""Drive the webhook delivery retry tick from cron / systemd.

One of three triggers (this command, the localhost webhooks_tick POST view, or
folding services.run_due_deliveries into scheduler_beat). Use exactly one per
deployment. Enqueue-only: the actual HTTP send happens in db_worker.

    * * * * * cd /app && python manage.py run_due_deliveries
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.webhooks import services


class Command(BaseCommand):
    help = "Re-enqueue webhook deliveries whose retry time is due."

    def handle(self, *args: Any, **options: Any) -> None:
        claimed = services.run_due_deliveries()
        self.stdout.write(f"claimed {claimed} delivery(ies) for retry")
