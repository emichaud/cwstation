#!/usr/bin/env python3
"""Command-line CW decoder — runs the audio layer with no radio and no Django.

Examples:
  # decode a synthesized message (great for a quick sanity check)
  python tools/cw_decode.py --text "CQ CQ DE N0CALL K" --wpm 22

  # decode a WAV file (e.g. recorded off a receiver or exported from GQRX)
  python tools/cw_decode.py --wav signal.wav --tone 700

  # decode synthetic audio and also drive the logbook bridge
  python tools/cw_decode.py --text "CQ TEST DE W1AW 599" --wpm 20 --log

  # export a session JSON for the cw_view/ monitor
  python tools/cw_decode.py --text "599 TU 73" --wpm 26 --session out.json
"""
from __future__ import annotations
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audioengine import (synthesize_cw, CWDecoder, CWConfig, AudioEngineManager,
                         ArraySource, WavFileSource)
from audioengine.export import session_from_result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Decode CW from a WAV file or synthesized text.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--wav", help="path to a WAV file to decode")
    src.add_argument("--text", help="text to synthesize and decode (no radio needed)")
    ap.add_argument("--wpm", type=float, default=20.0, help="WPM for --text (default 20)")
    ap.add_argument("--tone", type=float, default=600.0, help="tone frequency Hz (default 600)")
    ap.add_argument("--snr", type=float, default=None, help="add noise at this SNR dB for --text")
    ap.add_argument("--prior", action="store_true", help="give the decoder the WPM as a prior")
    ap.add_argument("--log", action="store_true", help="run the logbook bridge and print drafts")
    ap.add_argument("--session", help="write a view session JSON to this path")
    args = ap.parse_args(argv)

    if args.wav:
        source = WavFileSource(args.wav)
        fs, truth = source.sample_rate, ""
    else:
        r = synthesize_cw(args.text, wpm=args.wpm, tone_hz=args.tone,
                          sample_rate=8000, snr_db=args.snr)
        source = ArraySource(r.audio, r.sample_rate)
        fs, truth = r.sample_rate, r.text

    cfg = CWConfig(tone_hz=args.tone, expected_wpm=(args.wpm if args.prior else None))
    decoder = CWDecoder(fs, cfg)

    mgr = AudioEngineManager(fs).add_demodulator(decoder)
    if args.log:
        from logbook.audio_bridge import CWLogBridge
        bridge = CWLogBridge()
        bridge.on_qso_ready = lambda d: print("  [qso draft]", json.dumps(d.as_dict()))
        mgr.subscribe(bridge.on_char)
    mgr.run_source(source)

    res = decoder.result
    if truth:
        print(f"sent   : {truth}")
    print(f"decoded: {res.text}")
    print(f"speed  : {res.wpm_final:.1f} wpm   tone: {args.tone:.0f} Hz   chars: {len(res.chars)}")

    if args.session:
        with open(args.session, "w") as f:
            json.dump(session_from_result(res, truth), f)
        print(f"session: wrote {args.session}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
