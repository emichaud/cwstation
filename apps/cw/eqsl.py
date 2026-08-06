"""eQSL.cc log upload.

eQSL accepts an ADIF file POSTed to ImportADIF.cfm with the account
credentials embedded as EQSL_USER / EQSL_PSWD header fields — the mechanism
every logging program uses. The response is HTML; the verdict line looks
like "Result: 2 out of 2 records added". Base URL is settings-overridable
so tests run against a fake server (no eQSL subscription required to
develop or verify this path).
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
import uuid

from django.conf import settings

DEFAULT_URL = "https://www.eqsl.cc/qslcard/ImportADIF.cfm"


class EQSLError(Exception):
    """eQSL unreachable or rejected the upload."""


def _base_url() -> str:
    return getattr(settings, "EQSL_UPLOAD_URL", DEFAULT_URL)


def _with_credentials(adif: str, username: str, password: str) -> str:
    header = (
        f"<EQSL_USER:{len(username)}>{username} "
        f"<EQSL_PSWD:{len(password)}>{password}\n"
    )
    if re.search(r"<eoh>", adif, re.IGNORECASE):
        return re.sub(r"<eoh>", lambda m: header + m.group(0), adif, count=1, flags=re.IGNORECASE)
    return header + "<EOH>\n" + adif


def upload_adif(username: str, password: str, adif: str) -> dict[str, object]:
    """Upload a log. Returns {"added": n, "message": verdict}. Raises
    EQSLError on transport failure or credential rejection."""
    payload = _with_credentials(adif, username, password)
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="Filename"; filename="log.adi"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        f"{payload}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    request = urllib.request.Request(
        _base_url(), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise EQSLError(f"eQSL unreachable ({e})") from e

    if re.search(r"(no such username|password.{0,20}incorrect|error:)", html, re.IGNORECASE):
        snippet = re.sub(r"<[^>]+>", " ", html)
        snippet = " ".join(snippet.split())[:160]
        raise EQSLError(f"eQSL rejected the upload: {snippet}")

    match = re.search(r"Result:\s*(\d+)\s*out of\s*(\d+)", html, re.IGNORECASE)
    if match:
        return {"added": int(match.group(1)), "of": int(match.group(2)),
                "message": match.group(0)}
    # eQSL sometimes words it differently; surface what it said
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())[:200]
    return {"added": None, "of": None, "message": text}
