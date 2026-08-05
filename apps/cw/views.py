"""CW Station views — monitor, decode, send, and the session CRUD."""
from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.views.generic import TemplateView

from apps.search.access import SearchAccess
from apps.smallstack.crud import Action, CRUDView

from . import services
from .forms import PracticeDecodeForm, RecordingDecodeForm, SendForm
from .models import CWSession


def _render_direction(value: str, obj: CWSession) -> SafeString:
    if obj.direction == CWSession.Direction.SENT:
        return mark_safe('<span class="cw-badge cw-badge-tx">TX</span>')
    return mark_safe('<span class="cw-badge cw-badge-rx">RX</span>')


def _render_callsigns(value: object, obj: CWSession) -> SafeString:
    # value arrives display-formatted (a JSON string) — read the real list.
    calls: list[str] = obj.callsigns or []
    if not calls:
        return mark_safe('<span style="color: var(--body-quiet-color);">—</span>')
    return mark_safe(" ".join(
        format_html('<span class="cw-badge cw-badge-call">{}</span>', c) for c in calls[:3]
    ))


def _render_wpm(value: float, obj: CWSession) -> str:
    return f"{value:.0f}" if value else "—"


class CWSessionCRUDView(CRUDView):
    model = CWSession
    fields = ["text"]
    url_base = "cw/sessions"
    paginate_by = 10
    mixins = [LoginRequiredMixin]
    actions = [Action.LIST, Action.DETAIL, Action.DELETE]

    list_fields = ["text", "direction", "source", "wpm", "callsigns", "created_at"]
    link_field = "text"
    field_transforms = {
        "text": "preview",
        "direction": _render_direction,
        "callsigns": _render_callsigns,
        "wpm": _render_wpm,
    }

    enable_search = True
    search_fields = ["text", "truth"]
    search_display = "text"
    search_subtitle = "direction"
    search_access = SearchAccess.AUTHENTICATED
    search_visibility = staticmethod(lambda qs, user: qs.filter(user=user))

    @classmethod
    def get_list_queryset(cls, qs: QuerySet[CWSession], request: HttpRequest) -> QuerySet[CWSession]:
        return qs.filter(user=request.user)

    @classmethod
    def _get_template_names(cls, suffix: str) -> list[str]:
        if suffix == "list":
            return ["cw/session_list.html"]
        if suffix == "detail":
            return ["cw/session_detail.html"]
        return super()._get_template_names(suffix)

    @classmethod
    def _make_view(cls, base_class: type) -> type:
        """Scope detail/delete to the owner — sessions are per-user data."""
        from apps.smallstack.crud import _CRUDDeleteBase, _CRUDDetailBase

        view_class = super()._make_view(base_class)

        if base_class in (_CRUDDetailBase, _CRUDDeleteBase):

            def get_queryset(self) -> QuerySet[CWSession]:
                return CWSession.objects.filter(user=self.request.user)

            view_class.get_queryset = get_queryset

        if base_class is _CRUDDetailBase:

            def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
                context = super(view_class, self).get_context_data(**kwargs)
                session: CWSession = self.object
                context["telemetry_json"] = json.dumps(session.telemetry or {})
                context["is_tx"] = session.direction == CWSession.Direction.SENT
                return context

            view_class.get_context_data = get_context_data

        return view_class


class MonitorView(LoginRequiredMixin, TemplateView):
    """The CW monitor — a paper-tape register replaying decoder sessions."""

    template_name = "cw/monitor.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        sessions = CWSession.objects.filter(user=self.request.user)[:12]
        replayable = [s for s in sessions if s.can_replay]
        context["sessions"] = replayable
        context["sessions_map"] = {str(s.pk): s.telemetry for s in replayable}
        context["stats"] = {
            "total": CWSession.objects.filter(user=self.request.user).count(),
            "rx": CWSession.objects.filter(
                user=self.request.user, direction=CWSession.Direction.RECEIVED
            ).count(),
            "best_wpm": CWSession.objects.filter(user=self.request.user).aggregate(
                m=Max("wpm")
            )["m"] or 0,
        }
        return context


class DecodeView(LoginRequiredMixin, TemplateView):
    """Decode CW — practice (synthesized) or off the air (WAV upload)."""

    template_name = "cw/decode.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("practice_form", PracticeDecodeForm())
        context.setdefault("recording_form", RecordingDecodeForm())
        return context

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        mode = request.POST.get("mode")
        if mode == "practice":
            form = PracticeDecodeForm(request.POST)
            if form.is_valid():
                session = services.decode_practice(
                    request.user,
                    text=form.cleaned_data["text"],
                    wpm=form.cleaned_data["wpm"],
                    tone_hz=form.cleaned_data["tone_hz"],
                    snr_db=form.cleaned_data["snr_db"],
                )
                return redirect(session)
            return self.render_to_response(self.get_context_data(practice_form=form))

        form = RecordingDecodeForm(request.POST, request.FILES)
        if form.is_valid():
            tone: float | None = (
                None if form.cleaned_data["auto_tone"] else form.cleaned_data["tone_hz"]
            )
            try:
                session = services.decode_recording(
                    request.user,
                    stream=form.cleaned_data["recording"],
                    tone_hz=tone,
                )
            except ValueError as exc:
                messages.error(request, f"Couldn't decode that file: {exc}")
                return self.render_to_response(self.get_context_data(recording_form=form))
            if not session.text:
                messages.warning(
                    request,
                    "No CW found — try setting the tone manually to the pitch "
                    "you hear (commonly 500–800 Hz).",
                )
            return redirect(session)
        return self.render_to_response(self.get_context_data(recording_form=form))


class SendView(LoginRequiredMixin, TemplateView):
    """Compose a message and key it into clean, click-free CW audio."""

    template_name = "cw/send.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("form", SendForm())
        return context

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        form = SendForm(request.POST)
        if form.is_valid():
            session = services.compose_send(
                request.user,
                text=form.cleaned_data["text"],
                wpm=form.cleaned_data["wpm"],
                tone_hz=form.cleaned_data["tone_hz"],
            )
            return redirect(session)
        return self.render_to_response(self.get_context_data(form=form))


def session_audio(request: HttpRequest, pk: int) -> HttpResponse:
    """Regenerated WAV for a synthesized session (practice or composed send)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    session = get_object_or_404(CWSession, pk=pk, user=request.user)
    if not session.has_audio:
        return HttpResponse("Audio for uploaded recordings is not stored.", status=404)
    blob = services.session_wav_bytes(session)
    response = HttpResponse(blob, content_type="audio/wav")
    response["Content-Disposition"] = f'inline; filename="cw-session-{pk}.wav"'
    return response
