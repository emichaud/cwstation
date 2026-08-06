"""Monitor a sound-card input and decode CW in real time.

Plug the radio's audio out (or a data interface / SDR's audio output) into a
sound-card input and run:

  # list capture devices, pick yours
  uv run python manage.py cw_monitor_live --list-devices

  # monitor: tone auto-detected from the first seconds of audio
  uv run python manage.py cw_monitor_live --device 2

  # fixed tone, WPM prior, save the run as a session for the admin user
  uv run python manage.py cw_monitor_live --tone 700 --wpm 25 --save admin

Decoded characters stream to stdout as they resolve. Ctrl-C stops the
monitor, flushes the decoder, prints a summary, and (with --save) stores the
run as a replayable CW session.

Requires the optional live-audio extra:  uv sync --extra live
"""
from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cw.engine.events import CharEvent
from apps.cw.engine.live import monitor_live
from apps.cw.engine.sources import SoundDeviceSource


class Command(BaseCommand):
    help = "Monitor a sound-card input and decode CW in real time (Ctrl-C to stop)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--list-devices", action="store_true",
                            help="list audio input devices and exit")
        parser.add_argument("--device", default=None,
                            help="input device index or name substring (default: system default)")
        parser.add_argument("--samplerate", type=int, default=8000,
                            help="capture sample rate Hz (default 8000 — plenty for CW audio)")
        parser.add_argument("--tone", type=float, default=None,
                            help="CW tone Hz (default: auto-detect from the first seconds)")
        parser.add_argument("--calibrate", type=float, default=3.0,
                            help="seconds of audio used for tone auto-detection (default 3)")
        parser.add_argument("--wpm", type=float, default=None,
                            help="optional WPM prior; the decoder still adapts")
        parser.add_argument("--save", metavar="USERNAME", default=None,
                            help="save the run as a CW session for this user on exit")
        parser.add_argument("--stream", metavar="USERNAME", default=None,
                            help="stream the decode to this user's live tape (/cw/live/)")
        parser.add_argument("--server", default="http://127.0.0.1:8005",
                            help="server base URL for --stream (default http://127.0.0.1:8005)")

    def handle(self, **options: Any) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:
            raise CommandError(
                "Live monitoring needs the 'sounddevice' package: uv sync --extra live"
            ) from e

        if options["list_devices"]:
            self.stdout.write(str(sd.query_devices()))
            return

        user_model = get_user_model()

        def resolve_user(username: str | None):
            if not username:
                return None
            try:
                return user_model.objects.get(username=username)
            except user_model.DoesNotExist as e:
                raise CommandError(f"No such user: {username!r}") from e

        user = resolve_user(options["save"])
        stream_user = resolve_user(options["stream"])

        device: int | str | None = options["device"]
        if isinstance(device, str) and device.isdigit():
            device = int(device)

        source = SoundDeviceSource(
            sample_rate=options["samplerate"], block_size=512, device=device
        )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"cw_monitor_live — device={device if device is not None else 'default'} "
            f"@ {options['samplerate']} Hz. Ctrl-C to stop."
        ))
        if options["tone"] is None:
            self.stdout.write(f"calibrating tone from the first {options['calibrate']:.0f}s of audio…")

        def on_tone(tone: float) -> None:
            self.stdout.write(f"tone   : auto-detected {tone:.0f} Hz")

        def on_char(ev: CharEvent) -> None:
            self.stdout.write(ev.char, ending="")
            self.stdout.flush()

        # --stream: mint a short-lived API token, POST diff batches to the
        # ingest endpoint, which relays them to the user's live-tape group.
        streamer_factory = None
        stream_token = None
        if stream_user is not None:
            import json
            import urllib.error
            import urllib.request

            from django.utils import timezone

            from apps.cw.engine.stream import ResultStreamer
            from apps.smallstack.models import APIToken

            stream_token, raw_key = APIToken.create_token(
                stream_user,
                name="cw_monitor_live stream",
                description="Auto-minted by cw_monitor_live; revoked on exit.",
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
                    if not warned[0]:  # don't spam once the server drops
                        self.stderr.write(f"\nstream : {ingest_url} unreachable ({e}); retrying quietly")
                        warned[0] = True

            def streamer_factory(result):  # noqa: F811
                return ResultStreamer(result, send_batch, source="sound card")

            self.stdout.write(f"stream : {ingest_url} → live tape for {stream_user.username}")

        # Receiver controls: the same knobs as the Simulator page (input gain,
        # squelch, AFC) steer this decoder live, polled from the DB.
        import time as _time

        from apps.cw import services
        from apps.cw.engine.cw import CWConfig

        cfg = CWConfig(afc=False, squelch_db=3.0)
        control_user = stream_user or user
        if control_user is not None:
            services.apply_receiver_controls(control_user, cfg)
            self.stdout.write(
                f"knobs  : gain {cfg.input_gain:g}× · squelch {cfg.squelch_db:g} dB · "
                f"AFC {'on' if cfg.afc else 'off'} — adjust live on /cw/live/"
            )

        streamer = None
        last_poll = [0.0]

        def on_tick(result) -> None:
            nonlocal streamer
            now = _time.monotonic()
            if control_user is not None and now - last_poll[0] > 0.5:
                last_poll[0] = now
                services.apply_receiver_controls(control_user, cfg)
            if streamer_factory is None:
                return
            if streamer is None:
                streamer = streamer_factory(result)
            streamer.tick()

        try:
            result = monitor_live(
                source,
                tone_hz=options["tone"],
                calibrate_s=options["calibrate"],
                expected_wpm=options["wpm"],
                on_char=on_char,
                on_tone=on_tone,
                on_tick=on_tick,
                config=cfg,
            )
            if streamer is not None:
                streamer.flush()
        except RuntimeError as e:  # SoundDeviceSource's guarded failures
            raise CommandError(str(e)) from e
        finally:
            if stream_token is not None:
                stream_token.revoke()

        self.stdout.write("")  # end the streaming line
        duration = (result.envelope_t[-1] if result.envelope_t else 0.0)
        self.stdout.write(
            f"copied : {len([c for c in result.chars if c.char != ' '])} chars "
            f"in {duration:.0f}s   speed: {result.wpm_final:.1f} wpm   "
            f"tone: {result.tone_hz:.0f} Hz"
        )

        if user is not None:
            if result.text.strip():
                from apps.cw import services

                session = services.save_live_session(user, result, result.tone_hz)
                self.stdout.write(self.style.SUCCESS(
                    f"saved  : session #{session.pk} — replay it on the CW Monitor"
                ))
            else:
                self.stdout.write("saved  : nothing decoded, session not created")
