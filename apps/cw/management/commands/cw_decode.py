"""Command-line CW decoder — runs the audio layer with no radio.

Examples:
  # decode a synthesized message (great for a quick sanity check)
  uv run python manage.py cw_decode --text "CQ CQ DE N0CALL K" --wpm 22

  # decode a WAV file (e.g. recorded off a receiver or exported from GQRX)
  uv run python manage.py cw_decode --wav signal.wav --tone 700

  # export a session JSON for the monitor view
  uv run python manage.py cw_decode --text "599 TU 73" --wpm 26 --session out.json
"""
from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cw.engine import (
    ArraySource,
    AudioEngineManager,
    AudioFileSource,
    CWConfig,
    CWDecoder,
    detect_tone,
    synthesize_cw,
)
from apps.cw.engine.bridge import CWLogBridge, QSODraft
from apps.cw.engine.export import session_from_result
from apps.cw.services import RECORDING_SQUELCH_DB


class Command(BaseCommand):
    help = "Decode CW from a WAV file or synthesized text (no radio needed)."

    def add_arguments(self, parser: CommandParser) -> None:
        src = parser.add_mutually_exclusive_group(required=True)
        src.add_argument("--wav", help="path to a recording to decode (WAV/MP3/FLAC/OGG)")
        src.add_argument("--text", help="text to synthesize and decode")
        parser.add_argument("--wpm", type=float, default=20.0, help="WPM for --text (default 20)")
        parser.add_argument(
            "--tone", type=float, default=None,
            help="tone frequency Hz (default: auto-detect for --wav, 600 for --text)",
        )
        parser.add_argument("--snr", type=float, default=None, help="add noise at this SNR dB for --text")
        parser.add_argument(
            "--squelch", type=float, default=None,
            help=f"SNR gate in dB against band noise (0 = off; default "
                 f"{RECORDING_SQUELCH_DB:g} for --wav, off for --text)",
        )
        parser.add_argument("--prior", action="store_true", help="give the decoder the WPM as a prior")
        parser.add_argument("--log", action="store_true", help="run the log bridge and print QSO drafts")
        parser.add_argument("--session", help="write a monitor session JSON to this path")

    def handle(self, **options: Any) -> None:
        tone: float | None = options["tone"]
        if options["wav"]:
            try:
                source: ArraySource = AudioFileSource(options["wav"])
            except (OSError, ValueError) as exc:
                raise CommandError(f"Couldn't read recording: {exc}") from exc
            fs, truth = source.sample_rate, ""
            if tone is None:
                tone = detect_tone(source.audio, fs)
                self.stdout.write(f"tone   : auto-detected {tone:.0f} Hz")
        else:
            tone = tone if tone is not None else 600.0
            r = synthesize_cw(
                options["text"], wpm=options["wpm"], tone_hz=tone,
                sample_rate=8000, snr_db=options["snr"],
            )
            source = ArraySource(r.audio, r.sample_rate)
            fs, truth = r.sample_rate, r.text

        # Recordings carry band noise between the marks; synthesized text does
        # not, so the gate defaults on only for --wav.
        squelch = options["squelch"]
        if squelch is None:
            squelch = RECORDING_SQUELCH_DB if options["wav"] else 0.0

        cfg = CWConfig(
            tone_hz=tone,
            expected_wpm=(options["wpm"] if options["prior"] else None),
            squelch_db=squelch,
        )
        decoder = CWDecoder(fs, cfg)
        mgr = AudioEngineManager(fs).add_demodulator(decoder)

        if options["log"]:
            bridge = CWLogBridge()

            def on_ready(draft: QSODraft) -> None:
                self.stdout.write(f"  [qso draft] {json.dumps(draft.as_dict())}")

            bridge.on_qso_ready = on_ready  # type: ignore[method-assign]
            mgr.subscribe(bridge.on_char)

        mgr.run_source(source)

        res = decoder.result
        if truth:
            self.stdout.write(f"sent   : {truth}")
        self.stdout.write(f"decoded: {res.text}")
        self.stdout.write(
            f"speed  : {res.wpm_final:.1f} wpm   tone: {tone:.0f} Hz   "
            f"squelch: {'off' if squelch <= 0 else f'{squelch:g} dB'}   "
            f"chars: {len(res.chars)}"
        )

        if options["session"]:
            with open(options["session"], "w") as f:
                json.dump(session_from_result(res, truth), f)
            self.stdout.write(f"session: wrote {options['session']}")
