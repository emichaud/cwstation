"""Custom API endpoints for the CW app."""
from __future__ import annotations

import re
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.smallstack.api import api_error, api_view

from .apitypes import APIRequest, operator
from .consumers import live_group_name
from .models import QSO, CWMacro, CWRig, CWSession, CWSimControl, QRZProfile, RadioStation
from .rigctl import RigctldClient, RigError

MACRO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")

# A batch is a fraction of a second of decode output; anything huge is not ours.
MAX_BATCH_BYTES = 256 * 1024


@api_view(methods=["POST"], require_auth=True)
def live_ingest(request: APIRequest) -> dict[str, Any] | Any:
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
        live_group_name(operator(request).pk),
        {"type": "cw.batch", "payload": payload},
    )
    return {"relayed": True}


def _macro_dict(m: CWMacro) -> dict[str, Any]:
    return {"id": m.pk, "name": m.name, "text": m.text, "order": m.order}


@api_view(methods=["GET", "POST"], require_auth=True)
def macros(request: APIRequest) -> dict[str, Any] | Any:
    """The operator's message memories.

    GET  → list (defaults seeded on first call)
    POST → create {name, text} | update {id, name?, text?} | delete {id, delete: true}
    """
    if request.method == "GET":
        CWMacro.seed_defaults(operator(request))
        return {"macros": [_macro_dict(m) for m in operator(request).cw_macros.all()]}

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)

    if data.get("id") is not None:
        macro = CWMacro.objects.filter(user=operator(request), pk=data["id"]).first()
        if macro is None:
            return api_error("No such macro", 404)
        if data.get("delete"):
            macro.delete()
            return {"deleted": True}
    else:
        macro = CWMacro(user=operator(request), order=operator(request).cw_macros.count())

    if "name" in data:
        name = str(data["name"]).strip().lstrip("/").lower()
        if not MACRO_NAME_RE.fullmatch(name):
            return api_error("Name must be 1-24 chars: letters, digits, dashes", 400)
        clash = CWMacro.objects.filter(user=operator(request), name=name).exclude(pk=macro.pk)
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


def _var_dict(v: Any) -> dict[str, Any]:
    return {"id": v.pk, "name": v.name, "value": v.value, "order": v.order}


@api_view(methods=["GET", "POST"], require_auth=True)
def station_vars(request: APIRequest) -> dict[str, Any] | Any:
    """The operator's custom tags — named values that expand as {name}.

    GET  → list
    POST → create {name, value} | update {id, name?, value?} | delete {id, delete: true}
    """
    from .models import RESERVED_VARIABLE_NAMES, CWVariable

    if request.method == "GET":
        return {"vars": [_var_dict(v) for v in operator(request).cw_variables.all()]}

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)

    if data.get("id") is not None:
        var = CWVariable.objects.filter(user=operator(request), pk=data["id"]).first()
        if var is None:
            return api_error("No such tag", 404)
        if data.get("delete"):
            var.delete()
            return {"deleted": True}
    else:
        var = CWVariable(user=operator(request), order=operator(request).cw_variables.count())

    if "name" in data:
        name = str(data["name"]).strip().lstrip("{").rstrip("}").lower()
        if not MACRO_NAME_RE.fullmatch(name):
            return api_error("Tag must be 1-24 chars: letters, digits, dashes", 400)
        if name in RESERVED_VARIABLE_NAMES:
            return api_error(f"{{{name}}} is filled by the station — pick another name", 400)
        clash = CWVariable.objects.filter(user=operator(request), name=name).exclude(pk=var.pk)
        if clash.exists():
            return api_error(f"{{{name}}} already exists", 409)
        var.name = name
    if "value" in data:
        value = str(data["value"]).strip()
        if not value or len(value) > 200:
            return api_error("Value must be 1-200 characters", 400)
        var.value = value
    if not var.name or not var.value:
        return api_error("Both a tag name and a value are required", 400)
    var.save()
    return _var_dict(var)


_RIG_CONFIG_FIELDS = ("enabled", "host", "port", "use_ptt", "audio_output", "ptt_lead_ms")
VALID_MODES = {"CW", "CWR", "USB", "LSB", "AM", "FM", "RTTY", "PKTUSB", "PKTLSB"}


@api_view(methods=["GET", "POST"], require_auth=True)
def rig(request: APIRequest) -> dict[str, Any] | Any:
    """The operator's rig: config + live CAT state.

    GET  → config + probe (freq/mode/PTT when rigctld is reachable)
    POST → partial config update, and/or CAT commands:
           {freq_hz: 14055000} tunes the rig, {mode: "CW"} sets mode
    """
    config, _ = CWRig.objects.get_or_create(user=operator(request))

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
def rig_tx(request: APIRequest) -> dict[str, Any] | Any:
    """Key a stored session through the rig: PTT on → audio → PTT off."""
    from . import transmit

    config = CWRig.objects.filter(user=operator(request), enabled=True).first()
    if config is None:
        return api_error("No rig configured — enable it on the Live page first.", 400)
    data = request.json
    if not isinstance(data, dict) or not data.get("session_id"):
        return api_error("session_id is required", 400)
    session = CWSession.objects.filter(user=operator(request), pk=data["session_id"]).first()
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
def log_quick(request: APIRequest) -> dict[str, Any] | Any:
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
        session = CWSession.objects.filter(user=operator(request), pk=data["session_id"]).first()
    freq_hz = data.get("freq_hz") or None
    qso = logbook.quick_log(
        operator(request), call,
        session=session,
        freq_hz=float(freq_hz) if freq_hz else None,
        source=str(data.get("source") or "session"),
    )
    return {
        "qso": _qso_dict(qso),
        "worked_before": logbook.worked_before(operator(request), call).exclude(pk=qso.pk).count(),
    }


def _filtered_qsos(request: APIRequest):
    from django.db.models import Q

    qs = QSO.objects.filter(user=operator(request))
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
def log_adif(request: APIRequest) -> Any:
    """Download the log (respecting the current search/band/mode filters)
    as an ADIF file — dates and times in UTC per the spec."""
    from django.http import HttpResponse

    from . import logbook
    from .services import station_callsign

    adif = logbook.adif_export(
        _filtered_qsos(request), station_call=station_callsign(operator(request))
    )
    response = HttpResponse(adif, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cw-station-log.adi"'
    return response


@api_view(methods=["GET"], require_auth=True)
def abbrev(request: APIRequest) -> dict[str, Any]:
    """The CW shorthand dictionary for tutor mode (static reference data)."""
    from .abbrev import LOOKUP

    return {"lookup": LOOKUP}


@api_view(methods=["GET"], require_auth=True)
def log_lookup(request: APIRequest) -> dict[str, Any] | Any:
    """Side-effect-free callsign intel for the QSO form: worked-before
    history and (when configured) a live QRZ record."""
    from . import logbook
    from .engine.bridge import CALLSIGN_RE

    call = (request.GET.get("call") or "").strip().upper()
    if not CALLSIGN_RE.fullmatch(call):
        return api_error("Not a callsign", 400)
    history = logbook.worked_before(operator(request), call)
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

    payload["qrz"] = lookup_for_user(operator(request), call)
    return payload


@api_view(methods=["POST"], require_auth=True)
def log_import(request: APIRequest) -> dict[str, Any] | Any:
    """Import an uploaded ADIF file into the operator's log. Duplicate
    QSOs (same call, same UTC minute) are skipped — re-imports are no-ops."""
    from . import logbook

    upload = request.FILES.get("adif")
    if upload is None:
        return api_error("Attach an ADIF file as 'adif'", 400)
    # size is Optional on UploadedFile; an unmeasurable part can't be bounded,
    # so treat it as oversized rather than reading it unchecked.
    if upload.size is None or upload.size > 10 * 1024 * 1024:
        return api_error("File too large (10 MB max)", 413)
    text = upload.read().decode("utf-8", "replace")
    return logbook.import_adif(operator(request), text)


@api_view(methods=["POST"], require_auth=True)
def log_eqsl_upload(request: APIRequest) -> dict[str, Any] | Any:
    """Upload not-yet-sent QSOs (respecting active filters) to eQSL.cc and
    mark them sent. {resend: true} re-sends already-marked QSOs too."""
    from django.utils import timezone

    from . import logbook
    from .eqsl import EQSLError, upload_adif
    from .models import EQSLProfile

    profile = EQSLProfile.objects.filter(user=operator(request)).first()
    if profile is None or not profile.username or not profile.get_password():
        return api_error("eQSL credentials aren't configured yet.", 400)

    data = request.json if isinstance(request.json, dict) else {}
    qsos = _filtered_qsos(request)
    if not data.get("resend"):
        qsos = qsos.filter(eqsl_sent_at__isnull=True)
    qsos = list(qsos)
    if not qsos:
        return {"uploaded": 0, "message": "Nothing new to send."}

    from .services import station_callsign

    adif = logbook.adif_export(qsos, station_call=station_callsign(operator(request)))
    try:
        result = upload_adif(profile.username, profile.get_password(), adif)
    except EQSLError as e:
        return api_error(str(e), 502)
    now = timezone.now()
    QSO.objects.filter(pk__in=[q.pk for q in qsos]).update(eqsl_sent_at=now)
    return {"uploaded": len(qsos), "message": result["message"]}


@api_view(methods=["GET", "POST"], require_auth=True)
def eqsl_config(request: APIRequest) -> dict[str, Any] | Any:
    """eQSL.cc credentials — write-only password, encrypted at rest."""
    from .models import EQSLProfile

    profile, _ = EQSLProfile.objects.get_or_create(user=operator(request))
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
def station_config(request: APIRequest) -> dict[str, Any] | Any:
    """The operator's station settings: callsign + default keying (WPM/sidetone).

    GET  → {callsign, resolved, wpm, tone_hz}
    POST → any of {callsign, wpm, tone_hz}; callsign blank clears it (falls back
           to the username). `resolved` is what fills {mycall} / ADIF."""
    from .services import station_callsign

    rig, _ = CWRig.objects.get_or_create(user=operator(request))
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        changed = []
        if "callsign" in data:
            call = str(data["callsign"]).strip().upper()
            if len(call) > 20:
                return api_error("Callsign too long (20 chars max)", 400)
            rig.callsign = call
            changed.append("callsign")
        if "wpm" in data:
            try:
                wpm = int(data["wpm"])
            except (TypeError, ValueError):
                return api_error("wpm must be a number", 400)
            if not 5 <= wpm <= 60:
                return api_error("wpm must be 5–60", 400)
            rig.send_wpm = wpm
            changed.append("send_wpm")
        if "tone_hz" in data:
            try:
                tone = int(data["tone_hz"])
            except (TypeError, ValueError):
                return api_error("tone_hz must be a number", 400)
            if not 300 <= tone <= 1200:
                return api_error("tone_hz must be 300–1200", 400)
            rig.send_tone_hz = tone
            changed.append("send_tone_hz")
        if changed:
            rig.save(update_fields=[*changed, "updated_at"])
    return {
        "callsign": rig.callsign,
        "resolved": station_callsign(operator(request)),
        "wpm": rig.send_wpm,
        "tone_hz": rig.send_tone_hz,
    }


@api_view(methods=["GET", "POST"], require_auth=True)
def qrz_config(request: APIRequest) -> dict[str, Any] | Any:
    """QRZ credentials. POST {username, password} saves the XML login;
    {logbook_key} saves the logbook.qrz.com API key; {unlink: true} clears
    everything; {test_call} runs a live lookup to prove the XML login."""
    from django.utils import timezone

    profile, _ = QRZProfile.objects.get_or_create(user=operator(request))
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return api_error("Expected a JSON object", 400)
        if data.get("unlink"):
            profile.username = ""
            profile.password = ""
            profile.session_key = ""
            profile.logbook_key = ""
            profile.save()
        else:
            if "username" in data:
                profile.username = str(data["username"]).strip()
            if "password" in data and data["password"]:
                profile.set_password(str(data["password"]))  # encrypted at rest
                profile.session_key = ""  # force re-auth with new credentials
            if "logbook_key" in data and data["logbook_key"]:
                profile.set_logbook_key(str(data["logbook_key"]).strip())
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
    unsent = QSO.objects.filter(user=operator(request), qrz_sent_at__isnull=True).count()
    return {
        "configured": bool(profile.username and profile.password),
        "username": profile.username,
        "logbook_configured": bool(profile.logbook_key),
        "unsent": unsent,
        "total": QSO.objects.filter(user=operator(request)).count(),
        "now": timezone.now().isoformat(),
    }


@api_view(methods=["POST"], require_auth=True)
def qrz_logbook_sync(request: APIRequest) -> dict[str, Any] | Any:
    """Sync with the operator's QRZ.com logbook.

    {action: "import"} → FETCH their QRZ log as ADIF and import it
    (duplicates skipped — safe to repeat).
    {action: "export"} → INSERT QSOs not yet sent to QRZ, mark them."""
    from django.utils import timezone

    from . import logbook
    from .qrzlogbook import QRZLogbookError, fetch_adif, insert_record

    profile = QRZProfile.objects.filter(user=operator(request)).first()
    key = profile.get_logbook_key() if profile else ""
    if not key:
        return api_error("No QRZ logbook API key configured yet.", 400)

    data = request.json if isinstance(request.json, dict) else {}
    action = data.get("action")

    if action == "import":
        try:
            adif = fetch_adif(key)
        except QRZLogbookError as e:
            return api_error(str(e), 502)
        # import_adif returns counts; the reply also names where they came
        # from, so widen rather than stuffing a string into a dict[str, int].
        stats: dict[str, Any] = dict(logbook.import_adif(operator(request), adif))
        stats["source"] = "qrz"
        return stats

    if action == "export":
        qsos = list(QSO.objects.filter(user=operator(request), qrz_sent_at__isnull=True))
        if not qsos:
            return {"exported": 0, "duplicates": 0, "message": "Nothing new to send."}
        exported = duplicates = 0
        now = timezone.now()
        try:
            for qso in qsos:
                record = logbook.adif_export([qso]).split("<EOH>", 1)[-1].strip()
                outcome = insert_record(key, record)
                if outcome == "duplicate":
                    duplicates += 1
                else:
                    exported += 1
                qso.qrz_sent_at = now
                qso.save(update_fields=["qrz_sent_at"])
        except QRZLogbookError as e:
            return api_error(
                f"{e} — {exported + duplicates} of {len(qsos)} sent before the failure", 502
            )
        return {"exported": exported, "duplicates": duplicates,
                "message": f"{exported} added, {duplicates} already there."}

    return api_error("action must be 'import' or 'export'", 400)


@api_view(methods=["GET"], require_auth=True)
def rig_setup_data(request: APIRequest) -> dict[str, Any]:
    """Everything the Rig Setup page needs: Hamlib presence, serial ports,
    the rig catalog, daemon status, and the operator's saved choices."""
    from . import rigdaemon

    config, _ = CWRig.objects.get_or_create(user=operator(request))
    return {
        "hamlib": rigdaemon.hamlib_status(),
        "serial_ports": rigdaemon.list_serial_ports(),
        "models": rigdaemon.list_models(),
        "daemon": rigdaemon.status(),
        "custom_images": _custom_rig_images(operator(request)),
        "saved": {
            "rig_model": config.rig_model,
            "serial_port": config.serial_port,
            "baud": config.baud,
            "port": config.port,
        },
    }


def _custom_rig_images(user: Any) -> dict[str, str]:
    """Rig photos keyed by Hamlib model number, shown instead of the built-in
    illustration. We ship no manufacturer photos (copyright); these are two
    seams for supplying your own, per-operator uploads winning over site-wide:

    1. **Per-operator uploads** (this user's own library) — stored under
       MEDIA_ROOT via the Rig Setup page. The copyright rests with the
       operator who uploaded the picture, not the product.
    2. **Site-wide override** — a licensed image dropped at
       `static/cw/rigs/<model>.png` (or webp/jpg) applies to every operator.
    """
    import os

    from django.conf import settings
    from django.templatetags.static import static as static_url

    from .models import CWRigPhoto

    out: dict[str, str] = {}
    # site-wide first, so a user's own upload overrides it
    rigs_dir = os.path.join(settings.BASE_DIR, "static", "cw", "rigs")
    if os.path.isdir(rigs_dir):
        for fname in os.listdir(rigs_dir):
            stem, ext = os.path.splitext(fname)
            if stem.isdigit() and ext.lower() in (".png", ".webp", ".jpg", ".jpeg"):
                out[stem] = static_url(f"cw/rigs/{fname}")
    if getattr(user, "is_authenticated", False):
        for photo in CWRigPhoto.objects.filter(user=user):
            out[str(photo.rig_model)] = photo.image.url
    return out


_RIG_PHOTO_EXTS = (".png", ".webp", ".jpg", ".jpeg", ".gif")


@api_view(methods=["POST"], require_auth=True)
def rig_photo(request: APIRequest) -> dict[str, Any] | Any:
    """Manage the operator's own photo for a rig model.

    Upload/replace: multipart POST with `model` (int) + `image` (file).
    Remove: POST {"action": "delete", "model": <int>} → reverts to illustration.
    """
    import os

    from .models import CWRigPhoto

    data = request.json if isinstance(request.json, dict) else {}

    # delete path (JSON)
    if data.get("action") == "delete":
        model_id = data.get("model")
        if not isinstance(model_id, int):
            return api_error("Provide the rig 'model' number to remove", 400)
        photo = CWRigPhoto.objects.filter(user=operator(request), rig_model=model_id).first()
        if photo:
            photo.delete()
        return {"model_id": str(model_id), "url": None}

    # upload path (multipart)
    upload = request.FILES.get("image")
    raw_model = request.POST.get("model")
    if upload is None or raw_model is None:
        return api_error("Attach an image as 'image' and the rig 'model' number", 400)
    if not raw_model.isdigit():
        return api_error("'model' must be a Hamlib model number", 400)
    model_id = int(raw_model)
    # An UploadedFile's name and size are both optional; a nameless or
    # size-less part can't be classified, so it's rejected with the same
    # message as a wrong extension rather than raising.
    if not upload.name or upload.size is None:
        return api_error("Use a PNG, JPG, WEBP, or GIF image", 415)
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in _RIG_PHOTO_EXTS:
        return api_error("Use a PNG, JPG, WEBP, or GIF image", 415)
    if upload.size > 8 * 1024 * 1024:
        return api_error("Image too large (8 MB max)", 413)

    photo = CWRigPhoto.objects.filter(user=operator(request), rig_model=model_id).first()
    if photo is None:
        photo = CWRigPhoto(user=operator(request), rig_model=model_id)
    else:
        photo.image.delete(save=False)  # drop the old file before replacing
    photo.image = upload
    photo.save()
    return {"model_id": str(model_id), "url": photo.image.url}


@api_view(methods=["POST"], require_auth=True)
def rig_daemon(request: APIRequest) -> dict[str, Any] | Any:
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
        model = int(data.get("model", ""))
    except (TypeError, ValueError):
        return api_error("A rig model number is required (try the dummy rig, model 1)", 400)
    serial_port = (data.get("serial_port") or "").strip() or None
    baud = data.get("baud") or None
    try:
        state = rigdaemon.start(model, serial_port=serial_port, baud=baud)
    except RigError as e:
        return api_error(str(e), 409)

    config, _ = CWRig.objects.get_or_create(user=operator(request))
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
def sim_control(request: APIRequest) -> dict[str, Any] | Any:
    """Read (GET) or update (POST, partial) the operator's live sim knobs.
    The running `cw_simulate` process polls this row and applies changes."""
    control, _ = CWSimControl.objects.get_or_create(user=operator(request))
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


def _station_dict(s: RadioStation) -> dict[str, Any]:
    return {"id": s.pk, "name": s.name, "freq_mhz": s.freq_mhz, "order": s.order}


@api_view(methods=["GET", "POST"], require_auth=True)
def radio_control(request: APIRequest) -> dict[str, Any] | Any:
    """The FM receiver: what hardware is present, and start/stop listening.

    GET  → {devices, running, freq_mhz, band, rtl_fm_present, sounddevice_present, log}
    POST → {action: "tune", freq_mhz} | {action: "seek", direction, freq_mhz} | {action: "stop"}
    """
    from . import radiodaemon

    if request.method == "GET":
        return {
            "devices": radiodaemon.list_devices(refresh=bool(request.GET.get("refresh"))),
            **radiodaemon.status(),
        }

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)
    action = data.get("action")

    if action == "stop":
        return {"devices": radiodaemon.list_devices(), **radiodaemon.stop()}

    if action not in ("tune", "seek"):
        return api_error("action must be 'tune', 'seek' or 'stop'", 400)
    try:
        freq = float(data.get("freq_mhz", ""))
    except (TypeError, ValueError):
        return api_error("A frequency in MHz is required", 400)
    device_index = int(data.get("device_index") or 0)

    try:
        if action == "seek":
            direction = str(data.get("direction") or "")
            state = radiodaemon.seek(direction, freq, device_index=device_index)
        else:
            # retune() serialises stop-then-start as one operation — two racing
            # tune clicks can't interleave and orphan an untracked rtl_fm.
            state = radiodaemon.retune(freq, device_index=device_index)
    except radiodaemon.RadioError as e:
        return api_error(str(e), 409)
    return {"devices": radiodaemon.list_devices(), **state}


@api_view(methods=["GET", "POST"], require_auth=True)
def radio_stations(request: APIRequest) -> dict[str, Any] | Any:
    """The operator's saved stations (the favourites strip).

    GET  → list
    POST → create {name, freq_mhz} | update {id, name?, freq_mhz?} | delete {id, delete: true}
    """
    from .radiodaemon import FM_BAND_MHZ

    if request.method == "GET":
        return {
            "stations": [_station_dict(s) for s in operator(request).radio_stations.all()]
        }

    data = request.json
    if not isinstance(data, dict):
        return api_error("Expected a JSON object", 400)

    if data.get("id") is not None:
        station = RadioStation.objects.filter(user=operator(request), pk=data["id"]).first()
        if station is None:
            return api_error("No such station", 404)
        if data.get("delete"):
            station.delete()
            return {"deleted": True}
    else:
        station = RadioStation(
            user=operator(request), order=operator(request).radio_stations.count()
        )

    if "name" in data:
        name = str(data["name"]).strip()
        if not name or len(name) > 32:
            return api_error("Name must be 1-32 characters", 400)
        clash = RadioStation.objects.filter(
            user=operator(request), name=name
        ).exclude(pk=station.pk)
        if clash.exists():
            return api_error(f"{name} already exists", 409)
        station.name = name
    if "freq_mhz" in data:
        try:
            freq = float(data["freq_mhz"])
        except (TypeError, ValueError):
            return api_error("Frequency must be a number", 400)
        low, high = FM_BAND_MHZ
        if not low <= freq <= high:
            return api_error(f"Frequency must be between {low:g} and {high:g} MHz", 400)
        station.freq_mhz = freq
    if not station.name or station.freq_mhz is None:
        return api_error("Both name and freq_mhz are required", 400)
    station.save()
    return _station_dict(station)
