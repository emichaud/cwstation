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

        user = None
        if options["save"]:
            user_model = get_user_model()
            try:
                user = user_model.objects.get(username=options["save"])
            except user_model.DoesNotExist as e:
                raise CommandError(f"No such user: {options['save']!r}") from e

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

        try:
            result = monitor_live(
                source,
                tone_hz=options["tone"],
                calibrate_s=options["calibrate"],
                expected_wpm=options["wpm"],
                on_char=on_char,
                on_tone=on_tone,
            )
        except RuntimeError as e:  # SoundDeviceSource's guarded failures
            raise CommandError(str(e)) from e

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
