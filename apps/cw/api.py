"""Custom API endpoints for the CW app."""
from __future__ import annotations

import re
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest

from apps.smallstack.api import api_error, api_view

from .consumers import live_group_name
from .models import QSO, CWMacro, CWRig, CWSession, CWSimControl, QRZProfile
from .rigctl import RigctldClient, RigError

MACRO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")

# A batch is a fraction of a second of decode output; anything huge is not ours.
MAX_BATCH_BYTES = 256 * 1024


@api_view(methods=["POST"], require_auth=True)
def live_ingest(request: HttpRequest) -> dict[str, Any] | Any:
    """Relay a live-decode batch from the capture process to the operator's
    open live-view tabs. Authenticated via Bearer token (the capture command
    mints a short-lived one) or session; the group is derived from the
    authenticated user, so a token can only feed its own operator's tape."""
    payload = request.json
    if not isinstance(payload, dict):
        return api_error("Expected a JSON object", 400)
    if len(request.body) > MAX_BATCH_BYTES:
        return api_error("Batch too large", 413)

    layer = get_channel_layer()
    if layer is None:
        return api_error("Channel layer not configured", 503)
    async_to_sync(layer.group_send)(
        live_group_name(request.user.pk),
        {"type": "cw.batch", "payload": payload},
    )
    return {"relayed": True}


def _macro_dict(m: CWMacro) -> dict[str, Any]:
    return {"id": m.pk, "name": m.name, "text": m.text, "order": m.order}


@api_view(methods=["GET", "POST"], require_auth=True)
def macros(request: HttpRequest) -> dict[str, Any] | Any:
    """The operator's message memories.

    GET  → list (defaults seeded on first call)
    POST → create {name, text} | update {id, name?, text?} | delete {id, delete: true}
    """
    if request.method == "GET":
        CWMacro.seed_defaults(request.user)
        return {"macros": [_macro_dict(m) for m in request.user.cw_macros.all()]}

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)

    if data.get("id") is not None:
        macro = CWMacro.objects.filter(user=request.user, pk=data["id"]).first()
        if macro is None:
            return api_error("No such macro", 404)
        if data.get("delete"):
            macro.delete()
            return {"deleted": True}
    else:
        macro = CWMacro(user=request.user, order=request.user.cw_macros.count())

    if "name" in data:
        name = str(data["name"]).strip().lstrip("/").lower()
        if not MACRO_NAME_RE.fullmatch(name):
            return api_error("Name must be 1-24 chars: letters, digits, dashes", 400)
        clash = CWMacro.objects.filter(user=request.user, name=name).exclude(pk=macro.pk)
        if clash.exists():
            return api_error(f"/{name} already exists", 409)
        macro.name = name
    if "text" in data:
        text = str(data["text"]).strip()
        if not text or len(text) > 280:
            return api_error("Text must be 1-280 characters", 400)
        macro.text = text
    if not macro.name or not macro.text:
        return api_error("Both name and text are required", 400)
    macro.save()
    return _macro_dict(macro)


_RIG_CONFIG_FIELDS = ("enabled", "host", "port", "use_ptt", "audio_output", "ptt_lead_ms")
VALID_MODES = {"CW", "CWR", "USB", "LSB", "AM", "FM", "RTTY", "PKTUSB", "PKTLSB"}


@api_view(methods=["GET", "POST"], require_auth=True)
def rig(request: HttpRequest) -> dict[str, Any] | Any:
    """The operator's rig: config + live CAT state.

    GET  → config + probe (freq/mode/PTT when rigctld is reachable)
    POST → partial config update, and/or CAT commands:
           {freq_hz: 14055000} tunes the rig, {mode: "CW"} sets mode
    """
    config, _ = CWRig.objects.get_or_create(user=request.user)

    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        for name in _RIG_CONFIG_FIELDS:
            if name in data:
                setattr(config, name, data[name])
        config.port = min(max(int(config.port or 4532), 1), 65535)
        config.ptt_lead_ms = min(max(int(config.ptt_lead_ms or 0), 0), 2000)
        config.save()
        # CAT commands ride the same POST so the panel is one form
        if config.enabled and ("freq_hz" in data or "mode" in data):
            try:
                with RigctldClient(config.host, config.port) as client:
                    if data.get("freq_hz"):
                        client.set_freq(int(data["freq_hz"]))
                    if data.get("mode"):
                        mode = str(data["mode"]).upper()
                        if mode not in VALID_MODES:
                            return api_error(f"Unknown mode {mode!r}", 400)
                        client.set_mode(mode)
            except (RigError, ValueError) as e:
                return api_error(str(e), 502)

    payload: dict[str, Any] = {name: getattr(config, name) for name in _RIG_CONFIG_FIELDS}
    payload["connected"] = False
    if config.enabled:
        try:
            with RigctldClient(config.host, config.port, timeout=1.5) as client:
                payload.update(client.status())
                payload["connected"] = True
        except RigError as e:
            payload["error"] = str(e)
    from .transmit import tx_state

    payload["tx"] = tx_state()
    return payload


@api_view(methods=["POST"], require_auth=True)
def rig_tx(request: HttpRequest) -> dict[str, Any] | Any:
    """Key a stored session through the rig: PTT on → audio → PTT off."""
    from . import transmit

    config = CWRig.objects.filter(user=request.user, enabled=True).first()
    if config is None:
        return api_error("No rig configured — enable it on the Live page first.", 400)
    data = request.json
    if not isinstance(data, dict) or not data.get("session_id"):
        return api_error("session_id is required", 400)
    session = CWSession.objects.filter(user=request.user, pk=data["session_id"]).first()
    if session is None:
        return api_error("No such session", 404)
    try:
        return transmit.transmit_session(config, session)
    except RigError as e:
        return api_error(str(e), 409)


def _qso_dict(q: QSO) -> dict[str, Any]:
    return {
        "id": q.pk, "call": q.call, "when": q.when.isoformat(), "mode": q.mode,
        "band": q.band, "freq_mhz": q.freq_mhz, "name": q.name, "qth": q.qth,
        "url": q.get_absolute_url(), "qrz_url": q.qrz_url,
    }


@api_view(methods=["POST"], require_auth=True)
def log_quick(request: HttpRequest) -> dict[str, Any] | Any:
    """Log a heard/worked callsign with everything the station knows:
    session link, session's mode, rig RF, and history/QRZ prefill."""
    from . import logbook

    data = request.json
    if not isinstance(data, dict) or not data.get("call"):
        return api_error("call is required", 400)
    call = str(data["call"]).strip().upper()
    from .engine.bridge import CALLSIGN_RE

    if not CALLSIGN_RE.fullmatch(call):
        return api_error(f"{call!r} doesn't look like a callsign", 400)

    session = None
    if data.get("session_id"):
        session = CWSession.objects.filter(user=request.user, pk=data["session_id"]).first()
    freq_hz = data.get("freq_hz") or None
    qso = logbook.quick_log(
        request.user, call,
        session=session,
        freq_hz=float(freq_hz) if freq_hz else None,
        source=str(data.get("source") or "session"),
    )
    return {
        "qso": _qso_dict(qso),
        "worked_before": logbook.worked_before(request.user, call).exclude(pk=qso.pk).count(),
    }


def _filtered_qsos(request: HttpRequest):
    from django.db.models import Q

    qs = QSO.objects.filter(user=request.user)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(call__icontains=q) | Q(name__icontains=q)
            | Q(qth__icontains=q) | Q(comment__icontains=q)
        )
    band = (request.GET.get("band") or "").strip()
    if band:
        qs = qs.filter(band=band)
    mode = (request.GET.get("mode") or "").strip()
    if mode:
        qs = qs.filter(mode=mode)
    return qs


@api_view(methods=["GET"], require_auth=True)
def log_adif(request: HttpRequest) -> Any:
    """Download the log (respecting the current search/band/mode filters)
    as an ADIF file — dates and times in UTC per the spec."""
    from django.http import HttpResponse

    from . import logbook

    adif = logbook.adif_export(
        _filtered_qsos(request), station_call=request.user.username.upper()
    )
    response = HttpResponse(adif, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cw-station-log.adi"'
    return response


@api_view(methods=["GET"], require_auth=True)
def log_lookup(request: HttpRequest) -> dict[str, Any] | Any:
    """Side-effect-free callsign intel for the QSO form: worked-before
    history and (when configured) a live QRZ record."""
    from . import logbook
    from .engine.bridge import CALLSIGN_RE

    call = (request.GET.get("call") or "").strip().upper()
    if not CALLSIGN_RE.fullmatch(call):
        return api_error("Not a callsign", 400)
    history = logbook.worked_before(request.user, call)
    last = history.first()
    payload: dict[str, Any] = {
        "call": call,
        "worked": history.count(),
        "last": None,
        "qrz": None,
    }
    if last is not None:
        payload["last"] = {
            "when": last.when.isoformat(),
            "name": last.name, "qth": last.qth,
            "gridsquare": last.gridsquare, "country": last.country,
            "mode": last.mode, "band": last.band,
        }
    from .qrz import lookup_for_user

    payload["qrz"] = lookup_for_user(request.user, call)
    return payload


@api_view(methods=["POST"], require_auth=True)
def log_import(request: HttpRequest) -> dict[str, Any] | Any:
    """Import an uploaded ADIF file into the operator's log. Duplicate
    QSOs (same call, same UTC minute) are skipped — re-imports are no-ops."""
    from . import logbook

    upload = request.FILES.get("adif")
    if upload is None:
        return api_error("Attach an ADIF file as 'adif'", 400)
    if upload.size > 10 * 1024 * 1024:
        return api_error("File too large (10 MB max)", 413)
    text = upload.read().decode("utf-8", "replace")
    return logbook.import_adif(request.user, text)


@api_view(methods=["POST"], require_auth=True)
def log_eqsl_upload(request: HttpRequest) -> dict[str, Any] | Any:
    """Upload not-yet-sent QSOs (respecting active filters) to eQSL.cc and
    mark them sent. {resend: true} re-sends already-marked QSOs too."""
    from django.utils import timezone

    from . import logbook
    from .eqsl import EQSLError, upload_adif
    from .models import EQSLProfile

    profile = EQSLProfile.objects.filter(user=request.user).first()
    if profile is None or not profile.username or not profile.get_password():
        return api_error("eQSL credentials aren't configured yet.", 400)

    data = request.json if isinstance(request.json, dict) else {}
    qsos = _filtered_qsos(request)
    if not data.get("resend"):
        qsos = qsos.filter(eqsl_sent_at__isnull=True)
    qsos = list(qsos)
    if not qsos:
        return {"uploaded": 0, "message": "Nothing new to send."}

    adif = logbook.adif_export(qsos, station_call=request.user.username.upper())
    try:
        result = upload_adif(profile.username, profile.get_password(), adif)
    except EQSLError as e:
        return api_error(str(e), 502)
    now = timezone.now()
    QSO.objects.filter(pk__in=[q.pk for q in qsos]).update(eqsl_sent_at=now)
    return {"uploaded": len(qsos), "message": result["message"]}


@api_view(methods=["GET", "POST"], require_auth=True)
def eqsl_config(request: HttpRequest) -> dict[str, Any] | Any:
    """eQSL.cc credentials — write-only password, encrypted at rest."""
    from .models import EQSLProfile

    profile, _ = EQSLProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        if "username" in data:
            profile.username = str(data["username"]).strip()
        if "password" in data and data["password"]:
            profile.set_password(str(data["password"]))
        profile.save()
    return {"configured": bool(profile.username and profile.password),
            "username": profile.username}


@api_view(methods=["GET", "POST"], require_auth=True)
def qrz_config(request: HttpRequest) -> dict[str, Any] | Any:
    """QRZ XML-API credentials. POST {username, password} saves;
    POST {test_call} additionally runs a live lookup to prove them."""
    profile, _ = QRZProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        if "username" in data:
            profile.username = str(data["username"]).strip()
        if "password" in data and data["password"]:
            profile.set_password(str(data["password"]))  # encrypted at rest
        profile.session_key = ""  # force re-auth with new credentials
        profile.save()
        if data.get("test_call"):
            from .qrz import QRZError, authenticate, lookup

            try:
                key = authenticate(profile.username, profile.get_password())
                profile.session_key = key
                profile.save(update_fields=["session_key", "updated_at"])
                info = lookup(key, str(data["test_call"]).strip().upper())
                return {"configured": True, "test": info or {"error": "call not found"}}
            except QRZError as e:
                return api_error(str(e), 502)
    return {"configured": bool(profile.username and profile.password),
            "username": profile.username}


@api_view(methods=["GET"], require_auth=True)
def rig_setup_data(request: HttpRequest) -> dict[str, Any]:
    """Everything the Rig Setup page needs: Hamlib presence, serial ports,
    the rig catalog, daemon status, and the operator's saved choices."""
    from . import rigdaemon

    config, _ = CWRig.objects.get_or_create(user=request.user)
    return {
        "hamlib": rigdaemon.hamlib_status(),
        "serial_ports": rigdaemon.list_serial_ports(),
        "models": rigdaemon.list_models(),
        "daemon": rigdaemon.status(),
        "saved": {
            "rig_model": config.rig_model,
            "serial_port": config.serial_port,
            "baud": config.baud,
            "port": config.port,
        },
    }


@api_view(methods=["POST"], require_auth=True)
def rig_daemon(request: HttpRequest) -> dict[str, Any] | Any:
    """Start/stop the managed rigctld.

    {action: "start", model, serial_port?, baud?}  |  {action: "stop"}
    Starting also saves the choice and points the operator's rig config at
    the daemon, so the Rig panel and TX light up immediately."""
    from . import rigdaemon

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)
    action = data.get("action")

    if action == "stop":
        return {"daemon": rigdaemon.stop()}

    if action != "start":
        return api_error("action must be 'start' or 'stop'", 400)
    try:
        model = int(data.get("model"))
    except (TypeError, ValueError):
        return api_error("A rig model number is required (try the dummy rig, model 1)", 400)
    serial_port = (data.get("serial_port") or "").strip() or None
    baud = data.get("baud") or None
    try:
        state = rigdaemon.start(model, serial_port=serial_port, baud=baud)
    except RigError as e:
        return api_error(str(e), 409)

    config, _ = CWRig.objects.get_or_create(user=request.user)
    config.enabled = True
    config.host = "127.0.0.1"
    config.port = state["spec"]["tcp_port"]
    config.rig_model = model
    config.serial_port = serial_port or ""
    if baud:
        config.baud = int(baud)
    config.save()
    return {"daemon": state}


_CONTROL_FIELDS = ("noise_level", "input_gain", "squelch_db", "afc", "paused_signals")


@api_view(methods=["GET", "POST"], require_auth=True)
def sim_control(request: HttpRequest) -> dict[str, Any] | Any:
    """Read (GET) or update (POST, partial) the operator's live sim knobs.
    The running `cw_simulate` process polls this row and applies changes."""
    control, _ = CWSimControl.objects.get_or_create(user=request.user)
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        for name in _CONTROL_FIELDS:
            if name in data:
                value = data[name]
                if not isinstance(value, (int, float, bool)):
                    return api_error(f"{name} must be a number or boolean", 400)
                setattr(control, name, value)
        control.clamped().save()
    return {name: getattr(control, name) for name in _CONTROL_FIELDS}
