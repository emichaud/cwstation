"""fldigi tap tests — a fake fldigi XML-RPC server (stdlib SimpleXMLRPCServer)
speaks the real protocol, so the tap is exercised end to end with no fldigi."""
from __future__ import annotations

import threading
import xmlrpc.client
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

import pytest

from apps.cw.engine.events import DecodeResult
from apps.cw.engine.fldigi import FldigiError, FldigiTap, tap_loop
from apps.cw.engine.stream import ResultStreamer


class FakeFldigi:
    """The handful of fldigi XML-RPC methods the tap uses."""

    def __init__(self) -> None:
        self.pending: list[bytes] = []
        self.carrier = 1000.0
        self.quality = 90.0

    def version(self) -> str:
        return "4.2.05"

    def modem_name(self) -> str:
        return "BPSK31"

    def carrier_get(self) -> float:
        return self.carrier

    def dial(self) -> float:
        return 14070000.0

    def quality_get(self) -> float:
        return self.quality

    def rx_get_data(self) -> xmlrpc.client.Binary:
        data = b"".join(self.pending)
        self.pending.clear()
        return xmlrpc.client.Binary(data)


@pytest.fixture
def fake_fldigi():
    fake = FakeFldigi()
    server = SimpleXMLRPCServer(("127.0.0.1", 0), logRequests=False, allow_none=True)
    server.register_function(fake.version, "fldigi.version")
    server.register_function(fake.modem_name, "modem.get_name")
    server.register_function(fake.carrier_get, "modem.get_carrier")
    server.register_function(fake.quality_get, "modem.get_quality")
    server.register_function(fake.dial, "main.get_frequency")
    server.register_function(fake.rx_get_data, "rx.get_data")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield fake
    server.shutdown()
    server.server_close()


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class TestFldigiTap:
    def test_connect_reports_modem_and_carrier(self, fake_fldigi):
        tap = FldigiTap(fake_fldigi.url)
        info = tap.connect()
        assert info["modem"] == "BPSK31"
        assert info["carrier_hz"] == 1000.0
        assert info["dial_hz"] == 14070000.0
        assert "4.2" in info["version"]

    def test_poll_converts_text_to_char_events(self, fake_fldigi):
        clock = FakeClock()
        tap = FldigiTap(fake_fldigi.url, clock=clock)
        tap.connect()
        fake_fldigi.pending.append(b"cq de k1abc")
        clock.t = 3.5
        events = tap.poll()
        assert "".join(e.char for e in events) == "CQ DE K1ABC"
        assert all(e.t_start == 3.5 for e in events)
        assert all(e.morse == "" for e in events)  # not a keyed mode
        assert events[0].confidence == pytest.approx(0.9)
        assert tap.poll() == []  # drained

    def test_newlines_become_word_gaps(self, fake_fldigi):
        tap = FldigiTap(fake_fldigi.url, clock=FakeClock())
        tap.connect()
        fake_fldigi.pending.append(b"K1ABC\r\n73")
        text = "".join(e.char for e in tap.poll())
        assert text == "K1ABC 73"

    def test_unreachable_raises_fldigi_error(self):
        tap = FldigiTap("http://127.0.0.1:1")  # nothing listens on port 1
        with pytest.raises(FldigiError, match="fldigi at"):
            tap.connect()


class TestTapLoop:
    def test_accumulates_and_streams(self, fake_fldigi):
        clock = FakeClock()
        tap = FldigiTap(fake_fldigi.url, clock=clock)
        tap.connect()
        result = DecodeResult()
        batches: list[dict[str, Any]] = []
        streamer = ResultStreamer(result, batches.append, interval_s=0.0)

        chunks = [b"CQ CQ DE ", b"W1AW ", b"PSK31 TEST "]

        def fake_sleep(_s: float) -> None:
            clock.t += 1.0
            if chunks:
                fake_fldigi.pending.append(chunks.pop(0))

        tap_loop(
            tap, result,
            on_tick=lambda r: streamer.tick(),
            poll_s=0.25, duration_s=5.0, sleep=fake_sleep,
        )
        assert result.text == "CQ CQ DE W1AW PSK31 TEST"
        assert result.engine == "fldigi:BPSK31"
        assert result.tone_hz == 1000.0
        # streamed batches cover the chars and identify the caller
        chars = [c for b in batches for c in b["chars"]]
        assert "".join(c["c"] for c in chars).strip() == result.text
        assert any("W1AW" in (b["meta"].get("calls") or []) for b in batches)

    def test_chars_only_streamer_respects_interval(self, fake_fldigi):
        # regression: with no envelope, stream time falls back to char time
        clock = FakeClock()
        tap = FldigiTap(fake_fldigi.url, clock=clock)
        tap.connect()
        result = DecodeResult()
        batches: list[dict[str, Any]] = []
        streamer = ResultStreamer(result, batches.append, interval_s=2.0)

        def fake_sleep(_s: float) -> None:
            clock.t += 1.0
            fake_fldigi.pending.append(b"E")

        tap_loop(
            tap, result,
            on_tick=lambda r: streamer.tick(),
            duration_s=6.0, sleep=fake_sleep,
        )
        streamer.flush()
        assert len(batches) >= 2  # interval gating advanced (not stuck at t=0)
        assert len(batches) < 7  # ...but did gate (not every tick)
