"""The hierarchy-aware logger-prefix matcher shared by the handler's exclusion
list and the log viewer's ``?logger=`` filter — see ``logger_match.py``."""

from __future__ import annotations

import pytest

from apps.telemetry.logger_match import matches_prefix, prefix_q
from apps.telemetry.models import LogRecord

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("logger_name", "prefix", "expected"),
    [
        ("apps.webhooks", "apps.webhooks", True),
        ("apps.webhooks.tasks", "apps.webhooks", True),
        ("apps.webhooks.tasks.deep", "apps.webhooks", True),
        ("apps.webhooks_admin", "apps.webhooks", False),
        ("apps.telemetry_report", "apps.telemetry", False),
        ("apps.other", "apps.webhooks", False),
        ("apps", "apps.webhooks", False),
    ],
)
def test_matches_prefix(logger_name, prefix, expected):
    assert matches_prefix(logger_name, prefix) is expected


def test_prefix_q_matches_the_same_records_as_matches_prefix():
    LogRecord.objects.create(ts=_now(), level="INFO", level_no=20, logger="apps.webhooks", message="parent")
    LogRecord.objects.create(
        ts=_now(), level="INFO", level_no=20, logger="apps.webhooks.tasks", message="child"
    )
    LogRecord.objects.create(
        ts=_now(), level="INFO", level_no=20, logger="apps.webhooks_admin", message="sibling"
    )

    messages = set(LogRecord.objects.filter(prefix_q("logger", "apps.webhooks")).values_list("message", flat=True))

    assert messages == {"parent", "child"}


def _now():
    from django.utils import timezone

    return timezone.now()
