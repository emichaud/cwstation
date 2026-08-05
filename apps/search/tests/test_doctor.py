"""search_doctor management command."""

from __future__ import annotations

import json as jsonlib
from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def _run(args=None):
    out = StringIO()
    call_command("search_doctor", *(args or []), stdout=out)
    return out.getvalue()


def test_doctor_runs_without_crashing():
    output = _run()
    assert "SmallStack Search — Doctor" in output
    assert "Summary:" in output


def test_doctor_json_emits_valid_json():
    output = _run(["--json"])
    parsed = jsonlib.loads(output)
    assert isinstance(parsed, list)
    assert all(isinstance(r, dict) for r in parsed)


def test_doctor_check_backend_present():
    output = _run(["--json"])
    parsed = jsonlib.loads(output)
    names = {r["name"] for r in parsed}
    assert "Search backend" in names
    assert "Search registry" in names
    assert "URL conf" in names


def test_doctor_explain_dumps_indexed_models():
    output = _run(["--explain"])
    # With or without registered views, should not crash.
    assert isinstance(output, str)


# ────────────────────────────────────────────────────────────────────────────
#  --audit — access-level table-of-contents + audience simulation
# ────────────────────────────────────────────────────────────────────────────


def test_doctor_audit_runs_and_lists_levels():
    """Human output shows summary, every access level, and audience block."""
    output = _run(["--audit"])
    assert "Access Audit" in output
    assert "STAFF" in output
    assert "AUTHENTICATED" in output
    assert "ANONYMOUS" in output
    assert "What each audience can find" in output
    assert "Anonymous visitor" in output
    assert "Authenticated user" in output
    assert "Staff user" in output


def test_doctor_audit_json_payload_shape():
    """JSON output exposes the same data in a machine-readable shape."""
    output = _run(["--audit", "--json"])
    parsed = jsonlib.loads(output)
    assert set(parsed) == {"summary", "by_level", "help_docs", "audience_can_find"}
    assert set(parsed["summary"]) == {"staff", "authenticated", "anonymous"}
    assert set(parsed["by_level"]) == {"staff", "authenticated", "anonymous"}
    assert set(parsed["audience_can_find"]) == {"anonymous", "authenticated", "staff"}
    assert parsed["help_docs"]["always_visible"] is True


def test_doctor_audit_audience_counts_are_monotonic():
    """Anonymous ≤ authenticated ≤ staff (each level is a strict superset)."""
    output = _run(["--audit", "--json"])
    parsed = jsonlib.loads(output)
    a = parsed["audience_can_find"]
    assert a["anonymous"] <= a["authenticated"] <= a["staff"]
