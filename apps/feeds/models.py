"""CollectedItem — the bundled landing table for consumed feed items.

Registering a source with no ``model=`` stores its items here, so the consume
side works with zero setup. Point a source at your own model (with a ``map=``)
when you want a domain-shaped row instead.
"""

from __future__ import annotations

from django.db import models


class CollectedItem(models.Model):
    """One item pulled from a registered feed source. Deduped on
    ``(source, guid)`` so re-polling a feed never creates duplicates."""

    source = models.CharField(max_length=100, db_index=True)
    guid = models.CharField(max_length=500)
    title = models.CharField(max_length=500, blank=True)
    link = models.URLField(max_length=1000, blank=True)
    summary = models.TextField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    published = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    # The full normalized item (incl. enclosures) so downstream keeps everything.
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-published", "-fetched_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "guid"], name="uniq_feed_source_guid"),
        ]
        indexes = [
            models.Index(fields=["source", "-published"]),
        ]
        verbose_name = "collected feed item"

    def __str__(self) -> str:
        return self.title or self.guid

    def get_absolute_url(self) -> str | None:
        return self.link or None
