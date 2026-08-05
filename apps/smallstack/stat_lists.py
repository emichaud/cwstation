"""Helpers for stat-card drill-down modal content.

A *stat list* is the standard body for a stat-card drill-down modal: a vertical
list of ``avatar · name · meta · count · chevron`` rows. Build rows with
:func:`stat_list_row` and wrap them with :func:`render_stat_list` to get the
``HttpResponse`` an ``hx-get`` endpoint returns.

This is the single, shared renderer for drill-down list content — apps should
not hand-roll ``format_html`` row markup. See ``docs/skills/dashboard-cards.md``.

Genuinely tabular drill-downs (e.g. a request log) may return a ``<table>``
instead; the modal styles both. Reach for a stat list when the content is a
list of entities you can click through to.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.http import HttpResponse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe

_CHEVRON = mark_safe('<span class="stat-list-chevron" aria-hidden="true">&rarr;</span>')


def stat_list_row(
    name,
    *,
    href: str | None = None,
    avatar=None,
    meta=None,
    count=None,
) -> SafeString:
    """Build one row for a stat drill-down list.

    Args:
        name: Primary label (the entity name). Required.
        href: Destination URL. Omit for a non-navigable row (renders a ``div``
            with no chevron).
        avatar: Monogram shown in the leading circle. Pass ``True`` to derive
            initials from ``name`` (first two characters, upper-cased). Falsy →
            no avatar circle.
        meta: Secondary muted text (e.g. an email or access level).
        count: Numeric pill, right-aligned with tabular figures.

    Returns:
        An HTML-safe row. Feed a sequence of these to :func:`render_stat_list`.
    """
    name_str = "" if name is None else str(name)
    if avatar is True:
        avatar = (name_str[:2] or "?").upper()

    avatar_html = format_html('<span class="stat-list-avatar" aria-hidden="true">{}</span>', avatar) if avatar else ""
    meta_html = format_html('<span class="stat-list-meta">{}</span>', meta) if meta else ""
    count_html = format_html('<span class="stat-list-count">{}</span>', count) if count is not None else ""
    chevron = _CHEVRON if href else ""

    inner = format_html(
        '{}<span class="stat-list-name">{}</span>{}{}{}',
        avatar_html,
        name_str,
        meta_html,
        count_html,
        chevron,
    )
    if href:
        return format_html('<a class="stat-list-row" href="{}">{}</a>', href, inner)
    return format_html('<div class="stat-list-row">{}</div>', inner)


def render_stat_list(rows: Iterable[SafeString], *, empty: str = "Nothing to show.") -> HttpResponse:
    """Wrap :func:`stat_list_row` results into the response an ``hx-get`` returns.

    Args:
        rows: Iterable of rows from :func:`stat_list_row`.
        empty: Message shown when ``rows`` is empty.
    """
    rows = list(rows)
    if rows:
        body = format_html('<div class="stat-list">{}</div>', format_html_join("", "{}", ((r,) for r in rows)))
    else:
        body = format_html('<p class="stat-list-empty">{}</p>', empty)
    return HttpResponse(body)
