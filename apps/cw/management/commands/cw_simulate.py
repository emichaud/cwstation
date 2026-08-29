"""Simulate a busy band — no radio required.

Generates real-time radio static with CW stations embedded in it (random
messages, pitches, speeds, signal strengths) and decodes it live, exactly as
`cw_monitor_live` would decode a sound card. Stream it to the Simulator page
and use the on-page knobs (noise, input gain, squelch, AFC) while it runs:

  uv run python manage.py cw_simulate --stream admin

The knobs are polled from the database twice a second, so slider moves on the
page take effect immediately. Ctrl-C stops the simulation; --save stores the
run as a session with the transmitted text as ground truth, so you get an
accuracy score for your level settings.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.cw import services
from apps.cw.engine import AudioEngineManager, CWConfig, CWDecoder
from apps.cw.engine.events import CharEvent, DecodeResult
from apps.cw.engine.simulate import SimulatedBandSource
from apps.cw.engine.stream import ResultStreamer


class Command(BaseCommand):
    help = "Simulate a band of CW stations in noise and decode it live (Ctrl-C to stop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--stream", metavar="USERNAME", default=None,
                            help="stream the decode to this user's live/simulator tape")
        parser.add_argument("--server", default=services.default_stream_server(),
                            help="server base URL for --stream (default: SITE_URL, "
                                 f"currently {services.default_stream_server()})")
        parser.add_argument("--save", metavar="USERNAME", default=None,
                            help="save the run (with ground truth) as a session on exit")
        parser.add_argument("--seed", type=int, default=None,
                            help="random seed (default: from the clock)")
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
        control_user = stream_user or save_user

        seed = options["seed"] if options["seed"] is not None else int(time.time()) % 2**31
        source = SimulatedBandSource(seed=seed, duration_s=options["duration"], realtime=True)
        cfg = CWConfig(tone_hz=600.0, afc=True, squelch_db=3.0)
        decoder = CWDecoder(source.sample_rate, cfg)
        mgr = AudioEngineManager(source.sample_rate).add_demodulator(decoder)

        def on_char(ev: CharEvent) -> None:
            self.stdout.write(ev.char, ending="")
            self.stdout.flush()

        mgr.subscribe(on_char)

        # streaming to the live tape (same path as cw_monitor_live)
        streamer: ResultStreamer | None = None
        stream_token = None
        if stream_user is not None:
            from apps.smallstack.models import APIToken

            stream_token, raw_key = APIToken.create_token(
                stream_user,
                name="cw_simulate stream",
                description="Auto-minted by cw_simulate; revoked on exit.",
                expires_at=timezone.now() + timedelta(hours=24),
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

            streamer = ResultStreamer(decoder.result, send_batch, source="simulator")
            self.stdout.write(f"stream : {ingest_url} → simulator tape for {stream_user.username}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"cw_simulate — seed {seed}, AFC on, squelch 3 dB. "
            f"Adjust knobs on /cw/sim/. Ctrl-C to stop."
        ))

        last_poll = 0.0
        result: DecodeResult = decoder.result
        try:
            for block in source.blocks():
                mgr.pump(block)
                if streamer is not None:
                    streamer.tick()
                now = time.monotonic()
                if control_user is not None and now - last_poll > 0.5:
                    last_poll = now
                    services.apply_receiver_controls(control_user, cfg, source=source)
        except KeyboardInterrupt:
            pass
        finally:
            mgr.finalize()
            if streamer is not None:
                streamer.flush()
            if stream_token is not None:
                stream_token.revoke()

        self.stdout.write("")
        truth = source.truth
        self.stdout.write(f"sent   : {truth}")
        self.stdout.write(f"decoded: {result.text}")
        if save_user is not None and result.text.strip():
            session = services.save_live_session(save_user, result, result.tone_hz)
            session.truth = truth
            session.save(update_fields=["truth"])
            acc = session.accuracy
            self.stdout.write(self.style.SUCCESS(
                f"saved  : session #{session.pk}"
                + (f" — accuracy {acc:.2f}" if acc is not None else "")
            ))

