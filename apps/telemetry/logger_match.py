"""Hierarchy-aware logger-name matching, shared by the handler and the viewer.

Python's logging hierarchy uses ``.`` as its separator: ``apps.webhooks`` is
the parent of ``apps.webhooks.delivery``, but ``apps.webhooks_admin`` is an
unrelated sibling that merely shares a string prefix. A plain
``startswith(prefix)`` conflates the two — this module is the one place that
gets it right, so the handler's exclusion list and the viewer's logger filter
can't drift apart and reintroduce that bug independently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import Q

# No top-level Django import: DatabaseLogHandler (apps/telemetry/handlers.py)
# imports matches_prefix() below, and handler instances are built by
# dictConfig during django.setup() — before the app registry is populated.
# prefix_q() is the only function that needs django.db.models.Q, so it imports
# it lazily; matches_prefix() stays safe to import at that point.


def matches_prefix(logger_name: str, prefix: str) -> bool:
    """True when ``logger_name`` is ``prefix`` itself or a hierarchical descendant.

    ``apps.webhooks`` matches ``apps.webhooks`` and ``apps.webhooks.delivery``,
    not ``apps.webhooks_admin``.
    """
    return logger_name == prefix or logger_name.startswith(prefix + ".")


def prefix_q(field: str, prefix: str) -> "Q":
    """The ``Q`` equivalent of :func:`matches_prefix`, for queryset filtering.

    ``Q(**{field: prefix}) | Q(**{f"{field}__startswith": f"{prefix}."})`` —
    the trailing ``.`` on the startswith half is what stops ``apps.webhooks``
    from also matching ``apps.webhooks_admin``.
    """
    from django.db.models import Q

    return Q(**{field: prefix}) | Q(**{f"{field}__startswith": f"{prefix}."})
