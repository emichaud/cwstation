"""AppConfig for the datasets module.

Thin registration layer (like apps.search / apps.mcp). Datasets self-register
when their app's ``datasets.py`` is autodiscovered by SmallStackConfig.ready()
(which runs first, in framework core). Here we register the opt-in MCP tools;
the REST surface is wired in urls.py.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger("smallstack.datasets")


class DatasetsConfig(AppConfig):
    name = "apps.datasets"
    # Namespaced label (like apps.runbook's "smallstack_runbook") so a downstream
    # app labelled "datasets" — a common BI noun — can't collide in INSTALLED_APPS.
    # Safe to set now: this app has no models/migrations, so there's no downstream
    # migration-history rename. Do NOT revert to plain "datasets".
    label = "smallstack_datasets"
    verbose_name = "Datasets"

    def ready(self) -> None:
        from django.conf import settings

        if not getattr(settings, "SMALLSTACK_DATASETS_ENABLED", True):
            return

        # Register MCP tools for every opted-in dataset. No-op if apps.mcp isn't
        # installed, and skipped entirely when MCP is off site-wide.
        if getattr(settings, "SMALLSTACK_MCP_ENABLED", True):
            try:
                from .mcp_tools import register_dataset_tools

                register_dataset_tools()
            except Exception:
                logger.exception("Failed to register dataset MCP tools")
