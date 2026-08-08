"""CI guard: Django ``{# #}`` comments must be single-line.

A multi-line ``{# … #}`` isn't parsed as a comment and renders as literal text
on the page — a bug we've shipped more than once. This test scans every project
template and fails with the offending file:line, so it can't creep back in.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_django_comments", REPO_ROOT / "scripts" / "check_django_comments.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finds_multiline_comment():
    """The detector flags a comment that spans lines but not a single-line one."""
    checker = _load_checker()
    assert checker.find_violations("{# ok single line #}\n<div></div>") == []
    assert checker.find_violations("{# starts here\n   and closes #}") == [1]


def test_no_multiline_django_comments_in_templates():
    checker = _load_checker()
    results = checker.scan(REPO_ROOT)
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path, lines in results.items()
        for line in lines
    ]
    assert not offenders, (
        "Multi-line {# #} Django comments render as text — use "
        "{% comment %}…{% endcomment %}:\n  " + "\n  ".join(offenders)
    )
