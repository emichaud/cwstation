"""End-to-end REST API smoke test against a running server.

Mirrors apps.mcp.management.commands.mcp_smoke so CI can run a single
verify loop for both protocols:

    make api-test     # this command
    make mcp-test     # the MCP equivalent

The doctrine is the same: mcp_doctor / `manage.py check` prove the code
is wired correctly in-process; this command proves the actual HTTP
server (proxy, middleware, WSGI, network) serves a working endpoint.

Mints a temp readonly APIToken, hits /api/schema/ (always available if
the API surface is registered), picks the first endpoint with GET in
methods, calls it with ?limit=3, revokes the token. Token revocation is
in a finally so Ctrl-C mid-run doesn't leak credentials.
"""

from __future__ import annotations

import json as jsonlib
import sys
import time
import urllib.error
import urllib.request

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.smallstack.models import APIToken


class Command(BaseCommand):
    help = (
        "End-to-end REST API smoke test: mint readonly token, hit "
        "/api/schema/ + a sample endpoint, revoke the token."
    )

    EXIT_CONNECT = 2
    EXIT_HTTP = 3
    EXIT_RESPONSE = 4

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default="http://localhost:8005",
            help=(
                "Base URL of the running server (default: http://localhost:8005). "
                "The schema and endpoint paths are appended to this."
            ),
        )
        parser.add_argument(
            "--user",
            default=None,
            help=(
                "Username to mint the token under. Defaults to the first "
                "staff user, then the first user, then errors out."
            ),
        )
        parser.add_argument(
            "--endpoint",
            default=None,
            help=(
                "Specific endpoint URL to call (full path, e.g. /api/widgets/). "
                "Defaults to the first GET-capable endpoint in the schema. "
                "Pass '__skip__' to skip the sample call (useful when no "
                "CRUDView has enable_api set yet)."
            ),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a machine-readable JSON summary instead of human text.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-step output; only emit on failure.",
        )

    # ---- entry point ------------------------------------------------------

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        user = self._resolve_user(options.get("user"))
        token, raw_key = self._mint_token(user)

        result: dict = {
            "base_url": base_url,
            "user": user.get_username(),
            "token_name": token.name,
            "steps": [],
        }
        try:
            self._step(
                result, "GET /api/schema/",
                lambda: self._http_get(f"{base_url}/api/schema/", raw_key),
                options=options,
            )
            last = result["steps"][-1]
            if last["status"] == "PASS":
                schema = last["response"]
                endpoint_url = self._pick_endpoint(schema, options.get("endpoint"))
                if endpoint_url is None:
                    result["steps"].append(
                        {
                            "name": "GET <sample endpoint>",
                            "status": "SKIP",
                            "reason": "no GET-capable endpoint found in schema",
                        }
                    )
                else:
                    # Append ?limit=3 so we don't pull a huge response.
                    sep = "&" if "?" in endpoint_url else "?"
                    full = f"{base_url}{endpoint_url}{sep}limit=3"
                    self._step(
                        result, f"GET {endpoint_url}",
                        lambda: self._http_get(full, raw_key),
                        options=options,
                    )
        finally:
            token.revoke()
            result["token_revoked"] = True

        statuses = [s["status"] for s in result["steps"]]
        ok = all(s in ("PASS", "SKIP") for s in statuses)
        result["status"] = "PASS" if ok else "FAIL"

        if options.get("json"):
            self.stdout.write(jsonlib.dumps(result, indent=2, default=str))
        elif options.get("quiet"):
            if not ok:
                self.stderr.write(self.style.ERROR("API smoke FAILED"))
        else:
            self.stdout.write("")
            mark = self.style.SUCCESS("PASS") if ok else self.style.ERROR("FAIL")
            self.stdout.write(f"Result: {mark} — token revoked, all steps logged")

        if not ok:
            failed = [s for s in result["steps"] if s["status"] == "FAIL"]
            if failed and "Could not reach" in failed[0].get("error", ""):
                sys.exit(self.EXIT_CONNECT)
            sys.exit(self.EXIT_RESPONSE)

    # ---- token management -------------------------------------------------

    def _resolve_user(self, username: str | None):
        User = get_user_model()
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User {username!r} does not exist.")
        user = User.objects.filter(is_staff=True).first() or User.objects.first()
        if user is None:
            raise CommandError("No users exist — create one (e.g. `make superuser`) first.")
        return user

    def _mint_token(self, user):
        name = f"api-smoke-{int(time.time())}"
        return APIToken.create_token(
            user=user, name=name, token_type="manual", access_level="readonly"
        )

    # ---- HTTP -------------------------------------------------------------

    def _http_get(self, url: str, raw_key: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {raw_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            # 4xx/5xx — the server is reachable but rejected the request.
            # Distinguish auth failures from generic HTTP errors so the
            # message tells the developer which knob to turn.
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise CommandError(
                    f"HTTP {exc.code} from {url}: {body}\n"
                    f"  → Auth rejected. The smoke uses a readonly token; if your "
                    f"CRUDView has StaffRequiredMixin you need a staff token. "
                    f"Mint one with `create_api_token <user> --access-level staff`."
                ) from exc
            raise CommandError(f"HTTP {exc.code} from {url}: {body}") from exc
        except urllib.error.URLError as exc:
            # Real connection-level failure (refused, DNS, timeout).
            raise CommandError(
                f"Could not reach {url}: {exc.reason}. Is the dev server running? (`make run`)"
            ) from exc

        if status != 200:
            raise CommandError(f"HTTP {status} from {url}: {payload[:200]}")

        try:
            return jsonlib.loads(payload) if payload else {}
        except jsonlib.JSONDecodeError as exc:
            raise CommandError(f"Non-JSON response from {url}: {payload[:200]!r}") from exc

    # ---- step orchestration -----------------------------------------------

    def _step(self, result, name: str, fn, *, options) -> None:
        started = time.perf_counter()
        try:
            response = fn()
            elapsed_ms = (time.perf_counter() - started) * 1000
            result["steps"].append(
                {
                    "name": name,
                    "status": "PASS",
                    "duration_ms": round(elapsed_ms, 2),
                    "response": response,
                }
            )
            if not options.get("quiet") and not options.get("json"):
                self.stdout.write(self.style.SUCCESS(f"[✓] {name}  ({elapsed_ms:.1f} ms)"))
        except CommandError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result["steps"].append(
                {"name": name, "status": "FAIL", "duration_ms": round(elapsed_ms, 2), "error": str(exc)}
            )
            if not options.get("quiet") and not options.get("json"):
                self.stdout.write(self.style.ERROR(f"[✗] {name}  ({elapsed_ms:.1f} ms): {exc}"))

    def _pick_endpoint(self, schema: dict, explicit: str | None) -> str | None:
        if explicit == "__skip__":
            return None
        if explicit:
            return explicit
        for ep in schema.get("endpoints", []) or []:
            methods = ep.get("methods", []) or []
            if "GET" in methods and ep.get("url"):
                return ep["url"]
        return None
