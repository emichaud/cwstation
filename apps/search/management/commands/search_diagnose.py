"""Search *performance* diagnostics — where is the time going?

Complements ``search_doctor`` (which checks configuration). Pass a query to
also time the end-to-end fan-out and, on Postgres, EXPLAIN ANALYZE a real FTS
query on the biggest table:

    manage.py search_diagnose
    manage.py search_diagnose "acme corp"
    manage.py search_diagnose "acme corp" --json

Runs anywhere the app runs — the tool of choice for a locked-down prod box
where you can't reach psql (e.g. `kamal app exec`).
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnose search performance (health, app timing, live query plan)."

    def add_arguments(self, parser):
        parser.add_argument(
            "query",
            nargs="?",
            help="Optional query — enables end-to-end timing + EXPLAIN.",
        )
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        from apps.search.diagnostics import collect_diagnostics, format_diagnostics_text

        data = collect_diagnostics(options.get("query"))
        if options.get("json"):
            self.stdout.write(json.dumps(data, indent=2, default=str))
        else:
            self.stdout.write(format_diagnostics_text(data))
