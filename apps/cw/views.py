"""CW Station views — monitor, decode, send, and the session CRUD."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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
from apps.smallstack.displays import CardDisplay, TableDisplay

from . import services
from .apitypes import operator
from .forms import PracticeDecodeForm, QSOForm, RecordingDecodeForm, SendForm
from .models import QSO, CWSession

# Mixins below call super().get_context_data() and read self.request, which only
# exist on the view they're mixed into. Giving them a TemplateView base while
# type-checking lets mypy see both; at runtime they stay plain object mixins so
# no subclass MRO changes.
if TYPE_CHECKING:
    _ViewBase = TemplateView
else:
    _ViewBase = object


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


def _render_session_text(value: object, obj: CWSession) -> str:
    """Table copy on one line. Newlines collapse to spaces; the column caps the
    visible width in CSS (ellipsis only when the copy is genuinely long), and
    the cell's title tooltip carries the full text. Capped to keep the DOM lean
    for the huge live-monitor sessions."""
    from django.utils.text import Truncator

    return Truncator(" ".join((obj.text or "").split())).chars(140)


def _render_created(value: object, obj: CWSession) -> str:
    """Clean local timestamp for the table (no microseconds, no tz popup)."""
    from django.utils import timezone

    dt = timezone.localtime(obj.created_at)
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt:%b} {dt.day}, {dt:%Y} · {hour}:{dt:%M} {dt:%p}"


class SessionCardDisplay(CardDisplay):
    """Roomy, readable session cards — the copy is the hero, with a TX/RX badge
    and clean metadata (source, speed, calls, full timestamp). Replaces the dense
    truncated table so the list reads easily at a glance."""

    name = "cards"
    supports_bulk = False
    item_template = "cw/session_card.html"

    def build_card(self, obj: CWSession, cfg: Any, request: HttpRequest) -> dict[str, Any]:
        from django.urls import reverse

        text = (obj.text or "").strip()
        return {
            "text": text or "(empty)",
            "is_tx": obj.direction == CWSession.Direction.SENT,
            "source_label": obj.get_source_display(),
            "wpm": f"{obj.wpm:.0f}" if obj.wpm else "",
            "calls": (obj.callsigns or [])[:5],
            "when": obj.created_at,
            "delete_url": reverse("cw/sessions-delete", args=[obj.pk]),
        }


class CWSessionCRUDView(CRUDView):
    model = CWSession
    fields = ["text"]
    url_base = "cw/sessions"
    paginate_by = 5
    mixins = [LoginRequiredMixin]
    actions = [Action.LIST, Action.DETAIL, Action.DELETE]

    # Roomy cards by default; the classic table is one toggle away.
    displays = [SessionCardDisplay(), TableDisplay()]
    # Quick-filter the list by how the pass was made.
    filter_fields = ["direction", "source"]

    list_fields = ["text", "direction", "source", "wpm", "callsigns", "created_at"]
    link_field = "text"
    # No fixed widths — the sessions table uses content-based (auto) layout (see
    # cw.css) so the copy column takes its natural width and the rest sit snug.
    field_transforms = {
        "text": _render_session_text,
        "direction": _render_direction,
        "callsigns": _render_callsigns,
        "wpm": _render_wpm,
        "created_at": _render_created,
    }

    enable_search = True
    search_fields = ["text", "truth"]
    search_display = "text"
    search_subtitle = "direction"
    search_access = SearchAccess.AUTHENTICATED
    search_visibility = staticmethod(lambda qs, user: qs.filter(user=user))

    @classmethod
    def get_list_queryset(cls, qs: QuerySet[CWSession], request: HttpRequest) -> QuerySet[CWSession]:
        return qs.filter(user=operator(request))

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
                return CWSession.objects.filter(user=operator(self.request))

            view_class.get_queryset = get_queryset

        if base_class is _CRUDDetailBase:

            def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
                # view_class is assembled at runtime by _make_view, so mypy has
                # no superclass to resolve this against — unavoidable for a
                # method attached to a dynamically built class.
                context = super(view_class, self).get_context_data(**kwargs)  # type: ignore[misc]
                session: CWSession = self.object
                context["telemetry_json"] = json.dumps(session.telemetry or {})
                context["is_tx"] = session.direction == CWSession.Direction.SENT
                return context

            view_class.get_context_data = get_context_data

        return view_class


def _render_qso_call(value: str, obj: QSO) -> SafeString:
    return format_html(
        '<span class="cw-mono" style="font-weight: 700;">{}</span> '
        '<a href="{}" target="_blank" rel="noopener" title="QRZ.com" '
        'style="color: var(--text-muted); font-size: 0.7rem; text-decoration: none;">qrz↗</a>',
        value, obj.qrz_url,
    )


def _render_qso_when(value: object, obj: QSO) -> SafeString:
    import datetime as _dt

    utc = obj.when.astimezone(_dt.timezone.utc)
    return format_html(
        '<span class="cw-log2"><b>{}</b><i>{}z</i></span>',
        utc.strftime("%d %b %y"), utc.strftime("%H:%M"),
    )


def _render_qso_freq(value: object, obj: QSO) -> SafeString:
    if not obj.band and not obj.freq_mhz:
        return mark_safe('<span style="color: var(--text-muted);">—</span>')
    band = format_html('<b class="cw-log-band">{}</b>', obj.band) if obj.band else ""
    freq = format_html("<i>{}</i>", f"{obj.freq_mhz:.4f}") if obj.freq_mhz else ""
    return format_html('<span class="cw-log2">{}{}</span>', band, freq)


def _render_qso_rst(value: object, obj: QSO) -> SafeString:
    return format_html(
        '<span class="cw-mono">{} <span style="color: var(--text-muted);">⁄</span> {}</span>',
        obj.rst_sent or "—", obj.rst_rcvd or "—",
    )


def _render_qso_station(value: object, obj: QSO) -> SafeString:
    if not obj.name and not obj.qth:
        return mark_safe('<span style="color: var(--text-muted);">—</span>')
    title = " · ".join(p for p in (obj.name, obj.qth, obj.country) if p)
    name = format_html("<b>{}</b>", obj.name) if obj.name else ""
    qth = format_html("<i>{}</i>", obj.qth) if obj.qth else ""
    return format_html('<span class="cw-log2 cw-logst" title="{}">{}{}</span>', title, name, qth)


def _render_qso_session(value: object, obj: QSO) -> SafeString:
    # Guard on the relation, not session_id: a QSO can carry an id whose row was
    # deleted, and that's the case that would raise here.
    if obj.session is None:
        return mark_safe('<span style="color: var(--text-muted);">—</span>')
    return format_html(
        '<a href="{}" title="Replay the tape" style="color: var(--link-color);">tape #{}</a>',
        obj.session.get_absolute_url(), obj.session_id,
    )


class QSOCardDisplay(CardDisplay):
    """Roomy log cards — the callsign is the hero, with band/mode, RST, the UTC
    timestamp, and who/where. Clicking edits; QRZ, the session tape, and delete
    sit in the corner. Mirrors the Sessions card list."""

    name = "cards"
    supports_bulk = False
    item_template = "cw/qso_card.html"

    def build_card(self, obj: QSO, cfg: Any, request: HttpRequest) -> dict[str, Any]:
        import datetime as _dt

        from django.urls import reverse

        utc = obj.when.astimezone(_dt.timezone.utc)
        station = " · ".join(p for p in (obj.name, obj.qth, obj.country) if p)
        return {
            "call": obj.call,
            "qrz_url": obj.qrz_url,
            "band": obj.band,
            "mode": obj.mode,
            "rst": f"{obj.rst_sent or '—'}/{obj.rst_rcvd or '—'}",
            "freq": f"{obj.freq_mhz:.3f}" if obj.freq_mhz else "",
            "when_date": f"{utc:%d %b %Y}",
            "when_time": f"{utc:%H:%M}",
            "station": station,
            "session_url": (reverse("cw/sessions-detail", args=[obj.session_id]) if obj.session_id else ""),
            "edit_url": reverse("cw/log-update", args=[obj.pk]),
            "delete_url": reverse("cw/log-delete", args=[obj.pk]),
            "confirmed": bool(obj.eqsl_sent_at or obj.qrz_sent_at),
        }


class LogbookCRUDView(CRUDView):
    model = QSO
    fields = [
        "call", "when", "freq_mhz", "mode", "rst_sent", "rst_rcvd",
        "name", "qth", "gridsquare", "country", "comment",
    ]
    url_base = "cw/log"
    paginate_by = 5
    mixins = [LoginRequiredMixin]
    form_class = QSOForm
    actions = [Action.LIST, Action.CREATE, Action.UPDATE, Action.DELETE]

    # Roomy log cards by default; the classic table is one toggle away.
    displays = [QSOCardDisplay(), TableDisplay()]

    list_fields = ["call", "when", "freq_mhz", "mode", "rst", "station", "session"]
    link_field = "call"
    field_transforms = {
        "call": _render_qso_call,
        "when": _render_qso_when,
        "freq_mhz": _render_qso_freq,
        "rst": _render_qso_rst,
        "station": _render_qso_station,
        "session": _render_qso_session,
    }

    enable_search = True
    search_fields = ["call", "name", "qth", "country", "comment"]
    search_display = "call"
    search_subtitle = "name"
    search_access = SearchAccess.AUTHENTICATED
    search_visibility = staticmethod(lambda qs, user: qs.filter(user=user))

    @classmethod
    def get_list_queryset(cls, qs: QuerySet[QSO], request: HttpRequest) -> QuerySet[QSO]:
        qs = qs.filter(user=operator(request)).select_related("session")
        band = (request.GET.get("band") or "").strip()
        if band:
            qs = qs.filter(band=band)
        mode = (request.GET.get("mode") or "").strip()
        if mode:
            qs = qs.filter(mode=mode)
        return qs

    @classmethod
    def on_form_valid(cls, request: HttpRequest, form: Any, obj: QSO, is_create: bool = False) -> None:
        obj.user = operator(request)
        obj.call = obj.call.upper()
        from .logbook import band_for_freq

        obj.band = band_for_freq(obj.freq_mhz)
        obj.save()

    @classmethod
    def _get_template_names(cls, suffix: str) -> list[str]:
        if suffix == "list":
            return ["cw/log_list.html"]
        if suffix in ("form", "create", "edit"):
            return ["cw/qso_form.html"]
        return super()._get_template_names(suffix)

    @classmethod
    def _make_view(cls, base_class: type) -> type:
        from apps.smallstack.crud import (
            _CRUDCreateBase,
            _CRUDDeleteBase,
            _CRUDListBase,
            _CRUDUpdateBase,
        )

        view_class = super()._make_view(base_class)

        if base_class is _CRUDCreateBase:
            # the framework's CreateView saves directly (on_form_valid only
            # fires on updates) — the owner must land before the INSERT
            def form_valid(self, form: Any) -> Any:
                from .logbook import band_for_freq

                form.instance.user = operator(self.request)
                form.instance.band = band_for_freq(form.cleaned_data.get("freq_mhz"))
                return super(view_class, self).form_valid(form)  # type: ignore[misc]  # method attached to a runtime-built view_class

            view_class.form_valid = form_valid

        if base_class in (_CRUDUpdateBase, _CRUDDeleteBase):

            def get_queryset(self) -> QuerySet[QSO]:
                return QSO.objects.filter(user=operator(self.request))

            view_class.get_queryset = get_queryset

        if base_class is _CRUDListBase:

            def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
                context = super(view_class, self).get_context_data(**kwargs)  # type: ignore[misc]  # method attached to a runtime-built view_class
                mine = QSO.objects.filter(user=operator(self.request))
                context["log_stats"] = {
                    "total": mine.count(),
                    "calls": mine.values("call").distinct().count(),
                }
                # .order_by() clears Meta.ordering, which otherwise rides into
                # the DISTINCT and duplicates every chip
                context["log_bands"] = list(
                    mine.exclude(band="").order_by("band").values_list("band", flat=True).distinct()
                )
                context["log_modes"] = list(
                    mine.order_by("mode").values_list("mode", flat=True).distinct()
                )
                context["active_band"] = self.request.GET.get("band", "")
                context["active_mode"] = self.request.GET.get("mode", "")
                return context

            view_class.get_context_data = get_context_data

        return view_class


class MonitorView(LoginRequiredMixin, TemplateView):
    """The CW monitor — a paper-tape register replaying decoder sessions."""

    template_name = "cw/monitor.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        sessions = CWSession.objects.filter(user=operator(self.request))[:12]
        replayable = [s for s in sessions if s.can_replay]
        context["sessions"] = replayable
        context["sessions_map"] = {str(s.pk): s.telemetry for s in replayable}
        context["stats"] = {
            "total": CWSession.objects.filter(user=operator(self.request)).count(),
            "rx": CWSession.objects.filter(
                user=operator(self.request), direction=CWSession.Direction.RECEIVED
            ).count(),
            "best_wpm": CWSession.objects.filter(user=operator(self.request)).aggregate(
                m=Max("wpm")
            )["m"] or 0,
        }
        return context


class ArchitectureView(LoginRequiredMixin, TemplateView):
    """The signal-flow blueprint — how audio comes in, gets decoded, and goes
    back out, and where the rig, simulator, and libraries fit. Reference +
    troubleshooting aid, reachable from Help and Resources."""

    template_name = "cw/architecture.html"


class _StationCallMixin(_ViewBase):
    """Adds the operator's station defaults to the context — the resolved
    callsign plus the default keying WPM/sidetone — so any page can seed a
    keyer (send popup, decode keyer, /send setup) without re-deriving them.

    `_ViewBase` is `TemplateView` only while type-checking; at runtime this
    stays a plain mixin so it doesn't alter any subclass's MRO.
    """

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        defaults = services.station_defaults(operator(self.request))
        context["station_call"] = defaults["call"]
        context["send_wpm"] = defaults["wpm"]
        context["send_tone_hz"] = defaults["tone_hz"]
        return context


class LiveView(_StationCallMixin, LoginRequiredMixin, TemplateView):
    """The live tape — renders decode batches pushed over the WebSocket."""

    template_name = "cw/live.html"


class RadioView(LoginRequiredMixin, TemplateView):
    """Broadcast FM receiver — tune, listen, and recall saved stations.

    Deliberately not part of the CW decode path: it drives `rtl_fm` directly and
    plays to the server's sound device. Renders a clean empty state when there's
    no dongle (which is always the case on a deployed instance)."""

    template_name = "cw/radio.html"


class AntennaSurveyView(LoginRequiredMixin, TemplateView):
    """The antenna bench — sweep bands, score them, compare runs.

    Swapping antennas is only measurable against the same bands at the same
    gain, so a run is saved with both; the page compares saved runs side by
    side rather than asking the operator to remember last time's numbers."""

    template_name = "cw/survey.html"


class RigSetupView(_StationCallMixin, LoginRequiredMixin, TemplateView):
    """The rig launcher — pick the serial port and rig model (the 'select the
    right modem' step), start/supervise rigctld, verify the link."""

    template_name = "cw/rig_setup.html"


class CallbookView(LoginRequiredMixin, TemplateView):
    """The QRZ.com integration home: account, lookup console, log sync."""

    template_name = "cw/callbook.html"


class SimulatorView(_StationCallMixin, LoginRequiredMixin, TemplateView):
    """The band simulator — the live tape plus level/AFC knobs that steer a
    running `cw_simulate` process."""

    template_name = "cw/sim.html"


class DecodeView(_StationCallMixin, LoginRequiredMixin, TemplateView):
    """Decode CW off the air (WAV upload), and key a message into a
    downloadable WAV (the keyer, sharing the send defaults + insert engine)."""

    template_name = "cw/decode.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from .engine.bridge import CALLSIGN_RE

        context = super().get_context_data(**kwargs)
        context.setdefault("practice_form", PracticeDecodeForm())
        context.setdefault("recording_form", RecordingDecodeForm())
        # ?to=CALL — the "reply to a heard station" path lands on the keyer with
        # a standard reply pre-filled (the live pages reply in-place instead).
        to_call = (self.request.GET.get("to") or "").strip().upper()
        if CALLSIGN_RE.fullmatch(to_call):
            my_call = context.get("station_call") or operator(self.request).username.upper()
            context["reply_to"] = to_call
            context["keyer_prefill"] = f"{to_call} DE {my_call} {my_call} K"
        return context

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        mode = request.POST.get("mode")
        if mode == "practice":
            form = PracticeDecodeForm(request.POST)
            if form.is_valid():
                session = services.decode_practice(
                    operator(request),
                    text=form.cleaned_data["text"],
                    wpm=form.cleaned_data["wpm"],
                    tone_hz=form.cleaned_data["tone_hz"],
                    snr_db=form.cleaned_data["snr_db"],
                )
                return redirect(session)
            return self.render_to_response(self.get_context_data(practice_form=form))

        # distinct name: `form` above is bound to the practice form, and the two
        # are different types
        recording_form = RecordingDecodeForm(request.POST, request.FILES)
        if recording_form.is_valid():
            tone: float | None = (
                None if recording_form.cleaned_data["auto_tone"]
                else recording_form.cleaned_data["tone_hz"]
            )
            try:
                session = services.decode_recording(
                    operator(request),
                    stream=recording_form.cleaned_data["recording"],
                    tone_hz=tone,
                    squelch_db=recording_form.cleaned_data["squelch_db"],
                )
            except ValueError as exc:
                messages.error(request, f"Couldn't decode that file: {exc}")
                return self.render_to_response(
                    self.get_context_data(recording_form=recording_form)
                )
            if not session.text:
                messages.warning(
                    request,
                    "No CW found — try setting the tone manually to the pitch "
                    "you hear (commonly 500–800 Hz).",
                )
            return redirect(session)
        return self.render_to_response(self.get_context_data(recording_form=recording_form))


class SendView(_StationCallMixin, LoginRequiredMixin, TemplateView):
    """Compose a message and key it into clean, click-free CW audio.

    `?to=CALL` prefills a reply to a station the live monitor identified —
    the responder path from the live/simulator "heard on the band" chips."""

    template_name = "cw/send.html"

    # GET renders the setup page (defaults + callsign + macros + tags) from the
    # _StationCallMixin context; no compose form. POST is the JSON compose
    # endpoint used by the send popup, the decode keyer, and the setup preview.

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        from django.http import JsonResponse
        from django.urls import reverse

        wants_json = "application/json" in request.headers.get("Accept", "")
        form = SendForm(request.POST)
        if form.is_valid():
            session = services.compose_send(
                operator(request),
                text=form.cleaned_data["text"],
                wpm=form.cleaned_data["wpm"],
                tone_hz=form.cleaned_data["tone_hz"],
            )
            if wants_json:  # the live-page send sheet stays on the tape
                return JsonResponse({
                    "id": session.pk,
                    "text": session.text,
                    "audio_url": reverse("cw-session-audio", args=[session.pk]),
                    "detail_url": session.get_absolute_url(),
                })
            return redirect(session)
        if wants_json:
            return JsonResponse({"errors": form.errors}, status=400)
        return self.render_to_response(self.get_context_data(form=form))


def session_audio(request: HttpRequest, pk: int) -> HttpResponse:
    """Regenerated WAV for a synthesized session (practice or composed send)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    session = get_object_or_404(CWSession, pk=pk, user=operator(request))
    if not session.has_audio:
        return HttpResponse("Audio for uploaded recordings is not stored.", status=404)
    blob = services.session_wav_bytes(session)
    response = HttpResponse(blob, content_type="audio/wav")
    # ?dl=1 → download instead of inline playback (the decode keyer's "save WAV")
    disposition = "attachment" if request.GET.get("dl") else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="cw-{pk}.wav"'
    return response
