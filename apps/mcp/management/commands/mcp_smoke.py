"""End-to-end MCP smoke test against a running server.

Mints a temporary readonly token, hits the real `/mcp` endpoint over HTTP
with `tools/list` and a sample `tools/call`, then revokes the token. This
catches what `mcp_doctor`'s self-test can't — the doctor runs through
Django's test client (in-process), so it can't catch reverse-proxy bugs,
port collisions, WSGI quirks, or middleware that strips the auth header.

Designed as the target of `make mcp-test`. Exits non-zero on any failure
with a one-line cause; intended to be CI-friendly.

The token is named `mcp-smoke-<timestamp>` and revoked in a finally block
so a Ctrl-C mid-run doesn't leak credentials.
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
        "End-to-end MCP smoke test: mint readonly token, run tools/list + a "
        "sample tools/call against a running /mcp endpoint, revoke the token."
    )

    EXIT_CONNECT = 2
    EXIT_HTTP = 3
    EXIT_RPC = 4

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default="http://localhost:8005/mcp",
            help="MCP endpoint URL (default: http://localhost:8005/mcp).",
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
            "--tool",
            default=None,
            help=(
                "Specific tool name to call. Defaults to the first `list_*` "
                "tool in tools/list. Pass '__skip__' to skip the tools/call "
                "step entirely (useful when no tools are registered)."
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
        url = options["url"]
        user = self._resolve_user(options.get("user"))
        token, raw_key = self._mint_token(user)

        result: dict = {
            "url": url,
            "user": user.get_username(),
            "token_name": token.name,
            "steps": [],
        }
        try:
            self._step(
                result, "tools/list", lambda: self._rpc(url, raw_key, "tools/list"),
                options=options,
            )
            last = result["steps"][-1]
            if last["status"] == "PASS":
                tools = last["response"].get("result", {}).get("tools", [])
                tool_to_call = self._pick_tool(tools, options.get("tool"))
                if tool_to_call is None:
                    result["steps"].append(
                        {"name": "tools/call", "status": "SKIP", "reason": "no list_* tool registered"}
                    )
                else:
                    self._step(
                        result, f"tools/call {tool_to_call}",
                        lambda: self._rpc(
                            url, raw_key, "tools/call",
                            {"name": tool_to_call, "arguments": {"limit": 3}},
                        ),
                        options=options,
                    )
        finally:
            token.revoke()
            result["token_revoked"] = True

        # Final status
        statuses = [s["status"] for s in result["steps"]]
        ok = all(s in ("PASS", "SKIP") for s in statuses)
        result["status"] = "PASS" if ok else "FAIL"

        if options.get("json"):
            self.stdout.write(jsonlib.dumps(result, indent=2, default=str))
        elif options.get("quiet"):
            if not ok:
                self.stderr.write(self.style.ERROR("MCP smoke FAILED"))
        else:
            self.stdout.write("")
            mark = self.style.SUCCESS("PASS") if ok else self.style.ERROR("FAIL")
            self.stdout.write(f"Result: {mark} — token revoked, all steps logged")

        if not ok:
            # Exit code: distinguish connection failures (developer probably
            # forgot to start the server) from other RPC errors.
            failed = [s for s in result["steps"] if s["status"] == "FAIL"]
            if failed and "Could not reach" in failed[0].get("error", ""):
                sys.exit(self.EXIT_CONNECT)
            sys.exit(self.EXIT_RPC)

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
        name = f"mcp-smoke-{int(time.time())}"
        return APIToken.create_token(
            user=user, name=name, token_type="manual", access_level="readonly"
        )

    # ---- HTTP / JSON-RPC --------------------------------------------------

    def _rpc(self, url: str, raw_key: str, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params

        req = urllib.request.Request(
            url,
            data=jsonlib.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {raw_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            # 4xx/5xx — server reachable, request rejected. Auth failures
            # (401/403) are the most common false-conflation with
            # "server unreachable", so call them out explicitly.
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise CommandError(
                    f"HTTP {exc.code} from {url}: {body}\n"
                    f"  → Auth rejected. The smoke uses a readonly token; if any "
                    f"CRUDView has StaffRequiredMixin its tools require an "
                    f"`access_level=staff` token. Mint one with "
                    f"`create_api_token <user> --access-level staff`, "
                    f"or run the smoke against a non-staff CRUDView."
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
            parsed = jsonlib.loads(payload) if payload else {}
        except jsonlib.JSONDecodeError as exc:
            raise CommandError(f"Non-JSON response from {url}: {payload[:200]!r}") from exc

        if "error" in parsed:
            raise CommandError(
                f"JSON-RPC error {parsed['error'].get('code')}: {parsed['error'].get('message')}"
            )
        return parsed

    # ---- step orchestration ----------------------------------------------

    def _step(self, result, name: str, fn, *, options) -> None:
        started = time.perf_counter()
        try:
            response = fn()
            elapsed_ms = (time.perf_counter() - started) * 1000
            entry = {
                "name": name,
                "status": "PASS",
                "duration_ms": round(elapsed_ms, 2),
                "response": response,
            }
            result["steps"].append(entry)
            if not options.get("quiet") and not options.get("json"):
                self.stdout.write(self.style.SUCCESS(f"[✓] {name}  ({elapsed_ms:.1f} ms)"))
        except CommandError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result["steps"].append(
                {"name": name, "status": "FAIL", "duration_ms": round(elapsed_ms, 2), "error": str(exc)}
            )
            if not options.get("quiet") and not options.get("json"):
                self.stdout.write(self.style.ERROR(f"[✗] {name}  ({elapsed_ms:.1f} ms): {exc}"))

    def _pick_tool(self, tools: list[dict], explicit: str | None) -> str | None:
        if explicit == "__skip__":
            return None
        if explicit:
            return explicit
        for t in tools:
            if t.get("name", "").startswith("list_"):
                return t["name"]
        return None
