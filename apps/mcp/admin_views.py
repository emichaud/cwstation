"""Staff-only admin UI for the MCP subsystem.

Three pages plus one POST endpoint:

- Health (``mcp_admin:health``) — renders the same checks ``mcp_doctor``
  prints, as color-coded HTML cards.
- Tools (``mcp_admin:tools``) — browseable registry. Detail page shows
  full description + inputSchema.
- Activity (``mcp_admin:activity``) — recent /mcp requests, filtered out
  of apps.activity.RequestLog. Graceful banner if the activity app
  isn't installed.
- Self-test (``mcp_admin:self_test``) — POST-only. Mints + revokes a temp
  token, exercises tools/list / ping / notifications/initialized via the
  Django test client. Returns an htmx fragment.

The page-content code is intentionally thin: all the diagnostic work
lives on the existing ``Command`` class in mcp_doctor. We just rebind it
to an HTML surface.
"""

from __future__ import annotations

from typing import Any

from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.smallstack.mixins import StaffRequiredMixin
from apps.smallstack.stat_lists import render_stat_list, stat_list_row


def _build_mcp_report() -> list[dict]:
    """Run the mcp_doctor checks and return the report list.

    Same ``_check_*`` methods, same report shape the CLI prints — minus the
    self-test (which mints DB rows / makes HTTP calls). Shared by the health
    page and its stat-card drill-downs.
    """
    from apps.mcp.management.commands.mcp_doctor import Command

    cmd = Command()
    report: list[dict] = []
    cmd._check_mcp_package(report)
    cmd._check_settings(report)
    cmd._check_registry(report)
    cmd._check_urls(report)
    cmd._check_tokens(report)
    cmd._check_apitoken_admin(report)
    return report


class _AdminBase(StaffRequiredMixin, TemplateView):
    """Common base — staff gate plus shared context every page needs."""

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from apps.mcp.server import TOOL_REGISTRY

        ctx = super().get_context_data(**kwargs)
        ctx["tools_count"] = len(TOOL_REGISTRY)
        # warn_count + fail_count are reset by Health view; safe defaults here
        # so the tab badge logic doesn't crash on Tools / Activity.
        ctx.setdefault("warn_count", 0)
        ctx.setdefault("fail_count", 0)
        return ctx


class MCPAdminHealthView(_AdminBase):
    template_name = "mcp/admin/health.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "health"

        report = _build_mcp_report()
        ctx["report"] = report

        # Coarse summary numbers for the page header strip.
        ctx["pass_count"] = sum(1 for r in report if r["status"] == "PASS")
        ctx["warn_count"] = sum(1 for r in report if r["status"] == "WARN")
        ctx["fail_count"] = sum(1 for r in report if r["status"] == "FAIL")
        return ctx


class MCPAdminStatDetailView(StaffRequiredMixin, View):
    """htmx drill-down for the health stat cards.

    ``pass`` / ``warn`` / ``fail`` list the checks in that status; ``tools``
    lists the registered MCP tools (each links to its detail page).
    """

    def get(self, request, stat_type: str) -> HttpResponse:
        if stat_type == "tools":
            from apps.mcp.server import TOOL_REGISTRY

            rows = [
                stat_list_row(
                    tool.name,
                    href=reverse("mcp_admin:tool_detail", args=[tool.name]),
                    avatar=True,
                    meta=getattr(tool, "description", "") or "",
                )
                for tool in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name)
            ]
            return render_stat_list(rows, empty="No tools registered.")

        status = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(stat_type)
        if status is None:
            raise Http404("Unknown stat type")
        report = _build_mcp_report()
        rows = [
            stat_list_row(r["name"], meta=_detail_summary(r.get("detail")))
            for r in report
            if r["status"] == status
        ]
        empty = {"PASS": "No passing checks.", "WARN": "No warnings.", "FAIL": "No failures."}[status]
        return render_stat_list(rows, empty=empty)


def _detail_summary(detail) -> str:
    """Condense a check's ``detail`` (str or dict) into a one-line meta string."""
    if isinstance(detail, dict):
        return ", ".join(f"{k}: {v}" for k, v in detail.items())
    return "" if detail is None else str(detail)


class MCPAdminToolsView(_AdminBase):
    template_name = "mcp/admin/tools.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from apps.mcp.server import TOOL_REGISTRY

        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "tools"
        tools = sorted(TOOL_REGISTRY.values(), key=lambda t: t.name)
        ctx["tools"] = tools
        ctx["write_count"] = sum(1 for t in tools if t.write)
        ctx["read_only_count"] = sum(1 for t in tools if not t.write)
        return ctx


class MCPAdminToolDetailView(_AdminBase):
    template_name = "mcp/admin/tool_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        import json

        from apps.mcp.server import TOOL_REGISTRY

        ctx = super().get_context_data(**kwargs)
        name = self.kwargs["name"]
        if name not in TOOL_REGISTRY:
            raise Http404(f"No MCP tool named {name!r}")
        ctx["page"] = "tools"  # keep "Tools" tab active
        ctx["tool"] = TOOL_REGISTRY[name]
        # Pre-serialize the input schema so the template just prints it.
        ctx["schema_json"] = json.dumps(TOOL_REGISTRY[name].input_schema, indent=2)
        # Sample curl payload built from the tool's identity (we can't
        # know the project's host or the user's token; placeholders are
        # the honest answer).
        ctx["curl_payload"] = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": name, "arguments": {}}}
        )
        return ctx


class MCPAdminActivityView(_AdminBase):
    template_name = "mcp/admin/activity.html"

    PAGE_SIZE = 50
    SINCE_CHOICES = (("24h", "Last 24 hours"), ("7d", "Last 7 days"), ("all", "All time"))
    STATUS_CHOICES = (("any", "Any"), ("2xx", "2xx success"), ("4xx", "4xx client"), ("5xx", "5xx server"))
    METHOD_CHOICES = (("any", "Any"), ("GET", "GET"), ("POST", "POST"))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from datetime import timedelta

        from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "activity"
        ctx["activity_app_installed"] = self._activity_app_installed()
        ctx["filters_method"] = self.METHOD_CHOICES
        ctx["filters_status"] = self.STATUS_CHOICES
        ctx["filters_since"] = self.SINCE_CHOICES
        ctx["current"] = {
            "method": self.request.GET.get("method", "any"),
            "status_class": self.request.GET.get("status_class", "any"),
            "since": self.request.GET.get("since", "24h"),
            "user": self.request.GET.get("user", ""),
        }
        if not ctx["activity_app_installed"]:
            ctx["entries"] = []
            ctx["paginator"] = None
            return ctx

        # Importing here keeps the page importable even when apps.activity
        # is excluded from INSTALLED_APPS — the graceful-degradation banner
        # handles the no-data case.
        from apps.activity.models import RequestLog

        qs = RequestLog.objects.filter(path__startswith="/mcp").select_related("user", "api_token")

        method = ctx["current"]["method"]
        if method in {"GET", "POST"}:
            qs = qs.filter(method=method)

        status_class = ctx["current"]["status_class"]
        if status_class == "2xx":
            qs = qs.filter(status_code__gte=200, status_code__lt=300)
        elif status_class == "4xx":
            qs = qs.filter(status_code__gte=400, status_code__lt=500)
        elif status_class == "5xx":
            qs = qs.filter(status_code__gte=500, status_code__lt=600)

        since = ctx["current"]["since"]
        if since == "24h":
            qs = qs.filter(timestamp__gte=timezone.now() - timedelta(hours=24))
        elif since == "7d":
            qs = qs.filter(timestamp__gte=timezone.now() - timedelta(days=7))
        # "all" → no filter

        username = ctx["current"]["user"].strip()
        if username:
            qs = qs.filter(user__username__icontains=username)

        ordered = qs.order_by("-timestamp")
        paginator = Paginator(ordered, self.PAGE_SIZE)
        page_num = self.request.GET.get("page") or 1
        try:
            page_obj = paginator.page(page_num)
        except (EmptyPage, PageNotAnInteger):
            page_obj = paginator.page(1)
        ctx["entries"] = page_obj.object_list
        ctx["page_obj"] = page_obj
        ctx["paginator"] = paginator
        ctx["total"] = paginator.count
        # Per-status breakdown across the FILTERED set (the filter form
        # already narrowed `qs`; status_class doubles up but it's cheap
        # and the user might want to see "of these, how many failed?").
        ctx["count_2xx"] = ordered.filter(status_code__gte=200, status_code__lt=300).count()
        ctx["count_4xx"] = ordered.filter(status_code__gte=400, status_code__lt=500).count()
        ctx["count_5xx"] = ordered.filter(status_code__gte=500, status_code__lt=600).count()
        return ctx

    @staticmethod
    def _activity_app_installed() -> bool:
        from django.apps import apps as django_apps

        return any(c.label == "activity" for c in django_apps.get_app_configs())


class MCPAdminSelfTestView(StaffRequiredMixin, View):
    """POST-only endpoint backing the "Run self-test now" button.

    Triggers the same in-process self-test mcp_doctor runs at the end of
    its CLI report — mints a temp APIToken, makes three test-client
    JSON-RPC calls (tools/list, ping, notifications/initialized), then
    revokes the token. Returns an htmx fragment for inline swap.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import render

        from apps.mcp.management.commands.mcp_doctor import Command

        cmd = Command()
        report: list[dict] = []
        try:
            cmd._self_test(report)
        except Exception as exc:  # noqa: BLE001 — any failure becomes a FAIL row
            report.append({"name": "Self-test", "status": "FAIL", "detail": str(exc)})

        entry = report[0] if report else {"status": "FAIL", "detail": "self-test produced no result"}
        return render(
            request,
            "mcp/admin/_self_test_result.html",
            {"entry": entry},
        )
