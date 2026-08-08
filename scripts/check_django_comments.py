#!/usr/bin/env python
"""Fail on multi-line Django ``{# ... #}`` comments.

Django's ``{# #}`` comment is *single-line only* — a ``{#`` whose closing
``#}`` is on a later line is not treated as a comment and renders as literal
text in the page (a bug we've hit repeatedly). Use ``{% comment %}…{% endcomment %}``
for anything spanning more than one line.

Run directly (``python scripts/check_django_comments.py``) — exits non-zero and
lists offenders — or via the pytest guard in ``apps/website/test_template_hygiene.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def find_violations(text: str) -> list[int]:
    """Return the 1-indexed line numbers where a ``{#`` opens without a ``#}``
    closing it on the same line (i.e. the comment spans multiple lines)."""
    bad: list[int] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        i = 0
        while True:
            start = line.find("{#", i)
            if start == -1:
                break
            end = line.find("#}", start + 2)
            if end == -1:
                bad.append(lineno)
                break
            i = end + 2
    return bad


# Templates this project actually authors/edits. The framework apps (smallstack,
# activity, api, …) ship upstream; their templates are excluded so pre-existing
# upstream style isn't this project's CI failure. Add a dir here if you start
# hand-writing templates in another app.
PROJECT_TEMPLATE_ROOTS = (
    "templates",              # root tree, incl. our smallstack/* overrides
    "apps/cw/templates",      # the CW Station app
    "apps/website/templates",  # project-specific pages
)


def template_files(root: Path):
    """Every project-authored template under the roots above."""
    seen: set[Path] = set()
    for rel in PROJECT_TEMPLATE_ROOTS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.html")):
            if path not in seen:
                seen.add(path)
                yield path


def scan(root: Path) -> dict[Path, list[int]]:
    results: dict[Path, list[int]] = {}
    for path in template_files(root):
        lines = find_violations(path.read_text(encoding="utf-8", errors="replace"))
        if lines:
            results[path] = lines
    return results


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    results = scan(root)
    if not results:
        print("✓ No multi-line {# #} Django comments found.")
        return 0
    print("✗ Multi-line {# #} Django comments render as text — use {% comment %}…{% endcomment %}:")
    for path, lines in sorted(results.items()):
        for line in lines:
            print(f"  {path.relative_to(root)}:{line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
