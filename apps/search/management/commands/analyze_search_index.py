"""ANALYZE the tables behind the Postgres search index.

Run this on deploy. After a bulk ``search_vector`` write (an initial
backfill, an importer, a data migration) the planner's stats are stale
until autovacuum eventually catches up, and in the meantime it may
seq-scan ``search_vector @@ q`` instead of using the GIN index — turning
a 5ms query into a multi-second one. A plain ``ANALYZE`` is cheap and
fast (it samples, it doesn't rewrite), so it's safe on the hot deploy
path — unlike ``rebuild_search_index``, which is not.

No-op on SQLite / other engines (there are no planner stats to refresh).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Refresh Postgres planner stats for every searchable model's table."

    def handle(self, *args, **options):
        from apps.search.registry import all_views

        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.NOTICE(
                    f"Search index ANALYZE skipped — {connection.vendor} has no "
                    "planner stats to refresh."
                )
            )
            return

        views = list(all_views())
        if not views:
            self.stdout.write(self.style.WARNING("No indexed CRUDViews — nothing to do."))
            return

        analyzed = 0
        for view in views:
            table = view.model._meta.db_table
            try:
                with connection.cursor() as cur:
                    cur.execute(f'ANALYZE "{table}"')
            except Exception as exc:  # keep going — one bad table shouldn't abort deploy
                self.stdout.write(self.style.ERROR(f"  ANALYZE {table} failed: {exc}"))
                continue
            analyzed += 1
            self.stdout.write(f"  analyzed {view.model_label} ({table})")

        self.stdout.write(self.style.SUCCESS(f"ANALYZE complete — {analyzed} table(s)."))
