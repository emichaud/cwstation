"""Shared app-module autodiscovery.

Django auto-imports each app's ``models.py`` and ``admin.py`` but **not**
``views.py``. SmallStack's ``CRUDView`` subclasses live in ``views.py``, and
importing a module is what registers its CRUDViews into ``CRUDView._registry``
(via ``__init_subclass__``). Several subsystems walk that registry at startup —
Search (``enable_search``), the MCP factory (``enable_mcp``), the REST layer,
Explorer — so *something* must import every app's ``views.py`` before those walks
run.

This used to be a side effect of the MCP app's autodiscover, which silently coupled
Search (and any registry consumer) to ``SMALLSTACK_MCP_ENABLED``: turning MCP off
skipped the autodiscover and left the registry under-populated. The logic now lives
here and runs unconditionally from ``SmallStackConfig.ready()`` (the framework core
loads before any consumer), so the registry is fully populated regardless of
feature toggles.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger("smallstack.autodiscover")


def autodiscover_app_modules(module_names: tuple[str, ...], *, skip_label: str | None = None) -> list[str]:
    """Import ``<app>.<module>`` for every installed app × every name in ``module_names``.

    Returns the dotted paths successfully imported. A *missing* module (the app
    simply doesn't define it — most apps have no ``mcp_tools.py``) is skipped
    silently; an error *during* import (syntax/runtime failure) is logged but never
    re-raised — a crash here would take down ``AppConfig.ready()`` and the whole
    process. ``skip_label`` omits one app by its ``AppConfig.label`` (e.g. the
    caller's own, when it's already imported).
    """
    from django.apps import apps as django_apps

    imported: list[str] = []
    for app_config in django_apps.get_app_configs():
        if skip_label is not None and app_config.label == skip_label:
            continue
        for mod in module_names:
            dotted = f"{app_config.name}.{mod}"
            try:
                importlib.import_module(dotted)
                imported.append(dotted)
            except ImportError:
                pass
            except Exception:
                logger.warning("autodiscover failed to import %s", dotted, exc_info=True)
    return imported
