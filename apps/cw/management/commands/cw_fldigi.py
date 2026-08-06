"""Tap a running fldigi — PSK31/RTTY/etc. onto the live tape.

fldigi does the demodulating (best-in-class); this command adapts its
decoded text into the station's event stream, so the live view, heard-chip
responder, and sessions work for any fldigi mode with no other changes.

  # fldigi running with its XML-RPC server on (default port 7362)
  uv run python manage.py cw_fldigi --stream admin --server http://localhost:8005

Ctrl-C stops the tap; --save stores the copy as a session.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.cw.engine.events import CharEvent, DecodeResult
from apps.cw.engine.fldigi import FldigiError, FldigiTap, tap_loop
from apps.cw.engine.stream import ResultStreamer


class Command(BaseCommand):
    help = "Stream fldigi's decoded text (PSK31, RTTY, …) to the live tape (Ctrl-C to stop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--fldigi", default="http://127.0.0.1:7362",
                            help="fldigi XML-RPC URL (default http://127.0.0.1:7362)")
        parser.add_argument("--stream", metavar="USERNAME", default=None,
                            help="stream the copy to this user's live tape")
        parser.add_argument("--server", default="http://127.0.0.1:8005",
                            help="server base URL for --stream")
        parser.add_argument("--save", metavar="USERNAME", default=None,
                            help="save the run as a session on exit")
        parser.add_argument("--duration", type=float, default=None,
                            help="stop after this many seconds (default: run until Ctrl-C)")

    def handle(self, **options: Any) -> None:
        user_model = get_user_model()

        def resolve_user(username: str | None):
            if not username:
                return None
            try:
                return user_model.objects.get(username=username)
            except user_model.DoesNotExist as e:
                raise CommandError(f"No such user: {username!r}") from e

        stream_user = resolve_user(options["stream"])
        save_user = resolve_user(options["save"])

        tap = FldigiTap(options["fldigi"])
        try:
            info = tap.connect()
        except FldigiError as e:
            raise CommandError(
                f"{e}\nIs fldigi running with its XML-RPC server enabled "
                "(Configure → Misc → XML-RPC)?"
            ) from e
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"cw_fldigi — fldigi {info['version']} · modem {info['modem']} · "
            f"carrier {info['carrier_hz']:.0f} Hz · dial {info['dial_hz'] / 1e6:.4f} MHz"
        ))

        result = DecodeResult(engine=f"fldigi:{info['modem']}", tone_hz=info["carrier_hz"])

        streamer: ResultStreamer | None = None
        stream_token = None
        if stream_user is not None:
            from apps.smallstack.models import APIToken

            stream_token, raw_key = APIToken.create_token(
                stream_user,
                name="cw_fldigi stream",
                description="Auto-minted by cw_fldigi; revoked on exit.",
                expires_at=timezone.now() + timezone.timedelta(hours=24),
                access_level="auth",
            )
            ingest_url = options["server"].rstrip("/") + "/cw/live/ingest/"
            warned = [False]

            def send_batch(batch: dict) -> None:
                req = urllib.request.Request(
                    ingest_url,
                    data=json.dumps(batch).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {raw_key}",
                    },
                    method="POST",
                )
                try:
                    urllib.request.urlopen(req, timeout=2)
                    warned[0] = False
                except (urllib.error.URLError, OSError) as e:
                    if not warned[0]:
                        self.stderr.write(f"\nstream : {ingest_url} unreachable ({e}); retrying quietly")
                        warned[0] = True

            streamer = ResultStreamer(result, send_batch, source=f"fldigi {info['modem']}")
            self.stdout.write(f"stream : {ingest_url} → live tape for {stream_user.username}")

        def on_char(ev: CharEvent) -> None:
            self.stdout.write(ev.char, ending="")
            self.stdout.flush()

        def on_tick(r: DecodeResult) -> None:
            if streamer is not None:
                streamer.tick()

        started = time.monotonic()
        try:
            tap_loop(
                tap, result,
                on_char=on_char, on_tick=on_tick,
                duration_s=options["duration"],
            )
        finally:
            if streamer is not None:
                streamer.flush()
            if stream_token is not None:
                stream_token.revoke()

        self.stdout.write("")
        self.stdout.write(
            f"copied : {len([c for c in result.chars if c.char != ' '])} chars "
            f"in {time.monotonic() - started:.0f}s via {result.engine}"
        )
        if save_user is not None and result.text.strip():
            from apps.cw import services

            session = services.save_live_session(save_user, result, result.tone_hz)
            self.stdout.write(self.style.SUCCESS(f"saved  : session #{session.pk}"))
