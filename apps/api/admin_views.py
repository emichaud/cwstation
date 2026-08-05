"""Staff-only admin UI for the REST API surface.

Two pages plus one POST endpoint:

- Health (``api_admin:health``) — renders the same checks ``api_doctor``
  prints, as color-coded HTML cards.
- Activity (``api_admin:activity``) — per-endpoint group-by + threat panel
  + filterable ``/api/*`` RequestLog table. (Implemented in Phase 3.)
- Self-test (``api_admin:self_test``) — POST-only. Mints + revokes a temp
  token, hits /api/schema/ + /api/schema/openapi.json + first list
  endpoint via the Django test client. Returns an htmx fragment.

The diagnostic work lives on the existing ``Command`` class in
api_doctor; admin views rebind it to an HTML surface.
"""

from __future__ import annotations

from typing import Any

from django.http import Http404, HttpResponse
from django.views.generic import TemplateView, View

from apps.smallstack.mixins import StaffRequiredMixin
from apps.smallstack.stat_lists import render_stat_list, stat_list_row


def _build_api_report() -> list[dict]:
    """Run the api_doctor checks and return the report list.

    Same ``_check_*`` methods, same report shape the CLI prints — minus the
    self-test (HTTP + DB). Shared by the health page and its stat-card
    drill-downs.
    """
    from apps.api.management.commands.api_doctor import Command

    cmd = Command()
    report: list[dict] = []
    cmd._check_openapi_package(report)
    cmd._check_dependencies(report)
    cmd._check_registry(report)
    cmd._check_urls(report)
    cmd._check_swagger_redoc(report)
    cmd._check_openapi_validity(report)
    cmd._check_endpoint_consistency(report)
    cmd._check_orphans(report)
    cmd._check_token_auth(report)
    return report


def _detail_summary(detail) -> str:
    """Condense a check's ``detail`` (str or dict) into a one-line meta string."""
    if isinstance(detail, dict):
        return ", ".join(f"{k}: {v}" for k, v in detail.items())
    return "" if detail is None else str(detail)


class _AdminBase(StaffRequiredMixin, TemplateView):
    """Common base — staff gate plus shared context every page needs."""

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from apps.smallstack.api import _api_registry

        ctx = super().get_context_data(**kwargs)
        ctx["endpoint_count"] = len(_api_registry)
        ctx.setdefault("warn_count", 0)
        ctx.setdefault("fail_count", 0)
        return ctx


def _resolve_service_url(url_name: str) -> str | None:
    """Reverse an API service route, returning None if it isn't wired."""
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


# Ordered map of the runtime REST services the admin can link to. `kind`
# drives styling: primary = interactive UIs, schema = raw JSON, auth = API
# calls listed for reference (not "viewable" in a browser).
_API_SERVICES: tuple[tuple[str, str, str, str], ...] = (
    ("api-docs", "Swagger UI", "Interactive API explorer", "primary"),
    ("api-redoc", "ReDoc", "Reference documentation", "primary"),
    ("api-openapi-schema", "OpenAPI JSON", "OpenAPI 3.0.3 spec", "schema"),
    ("api-schema", "Schema JSON", "Endpoint registry + fields", "schema"),
    ("api-auth-token", "Auth: token", "POST — obtain a bearer token", "auth"),
    ("api-auth-me", "Auth: me", "GET — current token's user", "auth"),
)


class APIAdminHealthView(_AdminBase):
    template_name = "api/admin/health.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "health"
        # Quick "Open Swagger" link in the header — only when docs are wired.
        ctx["swagger_url"] = _resolve_service_url("api-docs")

        report = _build_api_report()
        ctx["report"] = report

        ctx["pass_count"] = sum(1 for r in report if r["status"] == "PASS")
        ctx["warn_count"] = sum(1 for r in report if r["status"] == "WARN")
        ctx["fail_count"] = sum(1 for r in report if r["status"] == "FAIL")
        return ctx


class APIAdminStatDetailView(StaffRequiredMixin, View):
    """htmx drill-down for the health stat cards: ``pass`` / ``warn`` / ``fail``
    list the individual checks in that status."""

    def get(self, request, stat_type: str) -> HttpResponse:
        status = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(stat_type)
        if status is None:
            raise Http404("Unknown stat type")
        report = _build_api_report()
        rows = [
            stat_list_row(r["name"], meta=_detail_summary(r.get("detail")))
            for r in report
            if r["status"] == status
        ]
        empty = {"PASS": "No passing checks.", "WARN": "No warnings.", "FAIL": "No failures."}[status]
        return render_stat_list(rows, empty=empty)


class APIAdminEndpointsView(_AdminBase):
    """Navigable map of the REST surface: service links + enabled models.

    Pure read-only introspection over ``_api_registry`` — the same source
    of truth ``api_doctor`` and the OpenAPI generator use. No DB access.
    """

    template_name = "api/admin/endpoints.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from apps.smallstack.api import _api_registry, _build_endpoint_schema

        ctx = super().get_context_data(**kwargs)
        ctx["page"] = "endpoints"

        # Service links — only those that actually resolve.
        services: list[dict[str, str]] = []
        for url_name, label, desc, kind in _API_SERVICES:
            url = _resolve_service_url(url_name)
            if url is not None:
                services.append({"label": label, "desc": desc, "url": url, "kind": kind})
        ctx["services"] = services

        # One row per CRUDView with enable_api=True. Per-row try/except so a
        # single misconfigured config can't 500 the whole page.
        resources: list[dict[str, Any]] = []
        for crud_config, list_url_name in _api_registry:
            try:
                schema = _build_endpoint_schema(crud_config, list_url_name)
                model = crud_config.model
                resources.append(
                    {
                        "model": schema["model"],
                        "verbose_name": str(model._meta.verbose_name).title(),
                        "list_url": schema["url"],
                        "detail_url": schema["url"].rstrip("/") + "/<int:pk>/",
                        "methods": schema["methods"],
                        "filter_count": len(schema["filter_fields"]),
                        "search_count": len(schema["search_fields"]),
                    }
                )
            except Exception:  # noqa: BLE001 — skip a broken config, keep the page up
                continue
        resources.sort(key=lambda r: r["model"].lower())
        ctx["resources"] = resources
        return ctx


class APIAdminActivityView(_AdminBase):
    """Per-endpoint group-by + threat panel + filterable RequestLog table.

    Three regions:
    1. Per-endpoint summary — top 10 by hit count with avg latency + error rate.
    2. Threat panel — heuristics from apps/api/threats.py (axes lockouts,
       auth bursts, path scanning, request bursts, scanner UAs, revoked
       token use). Empty card if nothing notable.
    3. Filterable RequestLog table — method / status_class / since / IP /
       user filters; paginated 50/page.

    Graceful degradation: if apps.activity not installed, regions 1 + 3
    show a banner; region 2 silently returns empty.
    """

    template_name = "api/admin/activity.html"
    PAGE_SIZE = 50
    SINCE_CHOICES = (
        ("1h", "Last hour"),
        ("24h", "Last 24 hours"),
        ("7d", "Last 7 days"),
        ("all", "All time"),
    )
    STATUS_CHOICES = (
        ("any", "Any"),
        ("2xx", "2xx success"),
        ("4xx", "4xx client"),
        ("5xx", "5xx server"),
    )
    METHOD_CHOICES = (
        ("any", "Any"),
        ("GET", "GET"),
        ("POST", "POST"),
        ("PUT", "PUT"),
        ("PATCH", "PATCH"),
        ("DELETE", "DELETE"),
    )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from datetime import timedelta

        from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
        from django.db.models import Avg, Count, Q
        from django.utils import timezone

        from apps.api.threats import collect_threats

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
            "ip": self.request.GET.get("ip", ""),
            "user": self.request.GET.get("user", ""),
            "scanner_only": self.request.GET.get("scanner_only", "") == "on",
        }

        # Threats: collect always (cheap when activity is empty; returns []
        # when apps.activity isn't installed).
        threats = collect_threats(window_hours=24)
        ctx["threats"] = threats
        ctx["threat_count"] = len(threats)
        ctx["threats_by_severity"] = {
            "high": [t for t in threats if t.severity == "high"],
            "medium": [t for t in threats if t.severity == "medium"],
            "low": [t for t in threats if t.severity == "low"],
        }

        if not ctx["activity_app_installed"]:
            ctx["per_endpoint"] = []
            ctx["entries"] = []
            ctx["paginator"] = None
            return ctx

        from apps.activity.models import RequestLog

        # Window cutoff for the per-endpoint summary + filtered table.
        since = ctx["current"]["since"]
        if since == "1h":
            cutoff = timezone.now() - timedelta(hours=1)
        elif since == "24h":
            cutoff = timezone.now() - timedelta(hours=24)
        elif since == "7d":
            cutoff = timezone.now() - timedelta(days=7)
        else:  # all
            cutoff = None

        base = RequestLog.objects.filter(path__startswith="/api")
        if cutoff is not None:
            base = base.filter(timestamp__gte=cutoff)

        # Region 1: per-endpoint summary across the current `since` window.
        per_endpoint = list(
            base.values("path")
            .annotate(
                hits=Count("id"),
                avg_ms=Avg("response_time_ms"),
                errors=Count("id", filter=Q(status_code__gte=400)),
            )
            .order_by("-hits")[:10]
        )
        for row in per_endpoint:
            row["error_rate"] = (row["errors"] / row["hits"] * 100) if row["hits"] else 0.0
            row["avg_ms"] = round(row["avg_ms"] or 0, 1)
        ctx["per_endpoint"] = per_endpoint

        # Region 3: filtered table.
        qs = base.select_related("user", "api_token")
        method = ctx["current"]["method"]
        if method != "any":
            qs = qs.filter(method=method)
        status_class = ctx["current"]["status_class"]
        if status_class == "2xx":
            qs = qs.filter(status_code__gte=200, status_code__lt=300)
        elif status_class == "4xx":
            qs = qs.filter(status_code__gte=400, status_code__lt=500)
        elif status_class == "5xx":
            qs = qs.filter(status_code__gte=500, status_code__lt=600)
        ip = ctx["current"]["ip"].strip()
        if ip:
            qs = qs.filter(ip_address=ip)
        username = ctx["current"]["user"].strip()
        if username:
            qs = qs.filter(user__username__icontains=username)
        if ctx["current"]["scanner_only"]:
            from apps.api.threats import SCANNER_UA_PATTERNS

            ua_q = Q()
            for pattern in SCANNER_UA_PATTERNS:
                ua_q |= Q(user_agent__icontains=pattern)
            qs = qs.filter(ua_q)

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

        # Tab badge sees the threat count too — surfaces from any tab.
        ctx["threat_count"] = len(threats)
        return ctx

    @staticmethod
    def _activity_app_installed() -> bool:
        from django.apps import apps as django_apps

        return any(c.label == "activity" for c in django_apps.get_app_configs())


class APIAdminSelfTestView(StaffRequiredMixin, View):
    """POST-only endpoint backing the "Run Self-Test" button.

    Mints a temp readonly APIToken, hits /api/schema/ + the OpenAPI JSON
    + the first list endpoint via the Django test client, revokes in a
    finally. Returns an htmx fragment.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import render

        from apps.api.management.commands.api_doctor import Command

        cmd = Command()
        report: list[dict] = []
        try:
            cmd._self_test(report)
        except Exception as exc:  # noqa: BLE001 — any failure becomes a FAIL row
            report.append({"name": "Self-test", "status": "FAIL", "detail": str(exc)})

        entry = report[0] if report else {"status": "FAIL", "detail": "self-test produced no result"}
        return render(request, "api/admin/_self_test_result.html", {"entry": entry})
