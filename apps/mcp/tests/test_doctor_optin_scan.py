"""mcp_doctor opt-in scan — AST detection, not substring matching.

Regression guard for the false positive where `enable_mcp = True` inside a
docstring / seed-content string (e.g. the runbook seed command) was reported as
an unregistered CRUDView opt-in.
"""

from __future__ import annotations

from apps.mcp.management.commands.mcp_doctor import _has_enable_mcp_classvar


def test_real_class_attribute_is_detected():
    src = "class TicketView(CRUDView):\n    enable_mcp = True\n"
    assert _has_enable_mcp_classvar(src) is True


def test_annotated_class_attribute_is_detected():
    src = "class TicketView(CRUDView):\n    enable_mcp: bool = True\n"
    assert _has_enable_mcp_classvar(src) is True


def test_marker_in_docstring_is_ignored():
    src = '''
class SeedCommand:
    """Teaching example embedded in docs:

        class TicketView(CRUDView):
            enable_mcp = True
    """
    help = "seed"
'''
    assert _has_enable_mcp_classvar(src) is False


def test_marker_in_string_literal_is_ignored():
    src = 'CONTENT = "class X(CRUDView):\\n    enable_mcp = True\\n"\n'
    assert _has_enable_mcp_classvar(src) is False


def test_marker_in_comment_is_ignored():
    src = "class X(CRUDView):\n    pass  # enable_mcp = True (not really)\n"
    assert _has_enable_mcp_classvar(src) is False


def test_enable_mcp_false_is_not_flagged():
    src = "class X(CRUDView):\n    enable_mcp = False\n"
    assert _has_enable_mcp_classvar(src) is False


def test_module_level_assignment_is_not_a_class_attribute():
    src = "enable_mcp = True\n"
    assert _has_enable_mcp_classvar(src) is False


def test_syntax_error_is_handled_gracefully():
    assert _has_enable_mcp_classvar("class X(:\n  enable_mcp = True") is False


def test_seed_runbook_command_is_not_flagged():
    """The concrete file that tripped the old substring scan."""
    from pathlib import Path

    seed = (
        Path(__file__).resolve().parents[3]
        / "apps/runbook/management/commands/seed_platform_runbook.py"
    )
    if not seed.exists():
        import pytest

        pytest.skip("runbook seed command not present")
    source = seed.read_text(encoding="utf-8")
    assert "enable_mcp = True" in source  # the substring IS there…
    assert _has_enable_mcp_classvar(source) is False  # …but not as a class attr
