"""`smallstack.W001` — the reported version must not be a stale build artifact.

The first assertion is the one that matters operationally: it must stay silent
on shipped defaults, because the harness smoke battery runs `check --deploy` and
a warning there is a failed gate for every future round.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from apps.smallstack import checks


def _ids(results) -> list[str]:
    return [r.id for r in results]


def test_silent_on_shipped_defaults():
    assert checks.check_reported_version_is_not_stale(None) == []


@pytest.mark.django_db  # some of Django's own deploy checks touch the connection
def test_no_smallstack_check_fires_under_check_deploy():
    from django.core.checks.registry import run_checks

    results = run_checks(include_deployment_checks=True)
    assert [r.id for r in results if str(r.id).startswith("smallstack.")] == []


def test_warns_when_the_installed_distribution_is_stale(monkeypatch, settings):
    """The real bug: a version bump committed without reinstalling.

    All three values are set by the test. The check infers "deliberately
    overridden" from `settings.SMALLSTACK_VERSION != installed` — sound in
    production, where the setting's default *is* the installed version — so a
    test that patches only two of the three couples itself to whatever version
    the repo happens to be on. This one originally hard-coded the then-current
    0.21.8 and started swallowing the warning the moment main reached 0.21.9;
    it would have done the same at the 0.22.0 release cut.
    """
    stale, declared = "1.0.0", "1.1.0"
    monkeypatch.setattr(checks, "installed_version", lambda _: stale)
    monkeypatch.setattr(checks, "_pyproject_version", lambda: declared)
    settings.SMALLSTACK_VERSION = stale  # not overridden: it tracks the install

    results = checks.check_reported_version_is_not_stale(None)

    assert _ids(results) == ["smallstack.W001"]
    assert stale in results[0].msg and declared in results[0].msg
    # The remedy must be the command that actually works here: a bare `uv sync`
    # installs the package but prunes the dev extras (pytest/ruff/mypy).
    assert "make setup" in results[0].hint
    assert "uv sync --extra dev" in results[0].hint


def test_silent_when_the_distribution_is_not_installed(monkeypatch):
    """Settings then read pyproject.toml directly, so no drift is possible."""

    def _missing(_):
        raise PackageNotFoundError

    monkeypatch.setattr(checks, "installed_version", _missing)
    monkeypatch.setattr(checks, "_pyproject_version", lambda: "1.1.0")
    assert checks.check_reported_version_is_not_stale(None) == []


def test_silent_when_pyproject_is_unreadable(monkeypatch):
    """A wheel-only deploy has nothing to compare against; don't guess."""
    monkeypatch.setattr(checks, "installed_version", lambda _: "1.0.0")
    monkeypatch.setattr(checks, "_pyproject_version", lambda: None)
    assert checks.check_reported_version_is_not_stale(None) == []


def test_silent_when_the_version_is_deliberately_overridden(monkeypatch, settings):
    """Same rule as tasks.W001: a deliberate opt-out is not a misconfiguration."""
    monkeypatch.setattr(checks, "installed_version", lambda _: "1.0.0")
    monkeypatch.setattr(checks, "_pyproject_version", lambda: "1.1.0")
    settings.SMALLSTACK_VERSION = "2026-09-api"  # decoupled from the package
    assert checks.check_reported_version_is_not_stale(None) == []


def test_pyproject_version_reads_the_real_file():
    """The helper is not mocked into always agreeing — it reads the repo."""
    import tomllib
    from pathlib import Path

    from django.conf import settings as dj_settings

    expected = tomllib.loads((Path(dj_settings.BASE_DIR) / "pyproject.toml").read_text())
    assert checks._pyproject_version() == expected["project"]["version"]
