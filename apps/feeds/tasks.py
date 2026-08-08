"""Background tasks for feeds. The scheduler autodiscovers this module.

``poll_feed_sources`` is the consume half's engine — a ``@scheduled`` job that
polls every registered source. The cadence lands in a ``ScheduledJob`` row that
ops can pause/retune from the scheduler UI without a redeploy. Runs the same
``collect_all()`` as ``manage.py collect_feeds`` (no separate implementation).
"""

from __future__ import annotations

from django.tasks import task

from apps.scheduler import scheduled


@scheduled(every="15m", name="Collect RSS feed sources")
@task()
def poll_feed_sources() -> list[dict]:
    from .collector import collect_all

    return collect_all()
