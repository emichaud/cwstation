"""Rebuild the passage-level (RAG) help index from filesystem markdown.

Unlike ``sync_help_index`` — one row per article, for "go to the page" — this
indexes one row per markdown SECTION so a tool/LLM gets a focused, cited
passage. Lexical-only (FTS5); no embeddings or extra dependencies. Run after
editing help docs, or to preview what a retrieval call would return.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Sync the passage-level (RAG) help index from filesystem markdown."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--query",
            help="After indexing, run this query and print the top passages.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.help.search import search_help_chunks, sync_help_rag_index

        count = sync_help_rag_index()
        if count == 0:
            self.stdout.write(self.style.WARNING(
                "Sync skipped — non-SQLite database or zero chunks."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"Indexed {count} help passages."))

        query = options.get("query")
        if not query:
            return

        hits = search_help_chunks(query, limit=5)
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(f'Top passages for "{query}":'))
        if not hits:
            self.stdout.write("  (no matches)")
            return
        for i, hit in enumerate(hits, 1):
            self.stdout.write(f"  {i}. {hit.display}")
            self.stdout.write(f"     source: {hit.subtitle}   rank: {hit.rank:.3f}")
            if hit.snippet:
                self.stdout.write(f"     …{hit.snippet}…")
