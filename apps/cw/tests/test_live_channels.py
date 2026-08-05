"""Channels live-tape tests: the streamer's diff batches, the WebSocket
consumer, and the ingest endpoint that relays between them."""
from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.cw.consumers import LiveTapeConsumer, live_group_name
from apps.cw.engine import SyntheticCWSource
from apps.cw.engine.live import monitor_live
from apps.cw.engine.stream import ResultStreamer

User = get_user_model()


class TestResultStreamer:
    def test_batches_cover_everything_exactly_once(self):
        batches: list[dict[str, Any]] = []
        streamer: list[ResultStreamer] = []

        def on_tick(result: Any) -> None:
            if not streamer:
                streamer.append(ResultStreamer(result, batches.append, interval_s=0.2))
            streamer[0].tick()

        src = SyntheticCWSource("CQ DE W1AW", wpm=20, tone_hz=600, block_size=512)
        result = monitor_live(src, tone_hz=600, on_tick=on_tick)
        streamer[0].flush()

        assert len(batches) > 1  # actually streamed, not one giant batch
        chars = [c for b in batches for c in b["chars"]]
        assert "".join(c["c"] for c in chars).strip() == result.text == "CQ DE W1AW"
        runs = [r for b in batches for r in b["key_runs"]]
        assert len(runs) == len(result.key_runs)
        env = [t for b in batches for t in b["env_t"]]
        assert len(env) == len(result.envelope_t)

    def test_meta_carries_tone_and_speed(self):
        src = SyntheticCWSource("PARIS", wpm=20, tone_hz=700, block_size=512)
        result = monitor_live(src, tone_hz=700)
        batches: list[dict[str, Any]] = []
        s = ResultStreamer(result, batches.append)
        s.flush()
        assert batches[0]["meta"]["tone_hz"] == 700
        assert batches[0]["meta"]["wpm"] == pytest.approx(20, abs=2.5)

    def test_meta_carries_identified_callsigns(self):
        src = SyntheticCWSource("CQ DE W1AW K", wpm=20, tone_hz=600, block_size=512)
        result = monitor_live(src, tone_hz=600)
        batches: list[dict[str, Any]] = []
        ResultStreamer(result, batches.append).flush()
        assert batches[0]["meta"]["calls"] == ["W1AW"]

    def test_partial_trailing_word_is_not_a_callsign(self):
        # mid-decode "CQ DE W1A" must not spawn a spurious W1A chip — only
        # completed words (followed by a space) are scanned
        from apps.cw.engine.events import CharEvent, DecodeResult

        r = DecodeResult(text="CQ DE W1A")
        r.chars.append(CharEvent("A", ".-", 0.0, 0.1, 20.0, 10.0))
        batches: list[dict[str, Any]] = []
        ResultStreamer(r, batches.append).flush()
        assert batches[0]["meta"]["calls"] == []

    def test_empty_tick_sends_nothing(self):
        from apps.cw.engine.events import DecodeResult

        batches: list[dict[str, Any]] = []
        s = ResultStreamer(DecodeResult(), batches.append)
        s.flush()
        assert batches == []


@pytest.mark.asyncio
class TestLiveTapeConsumer:
    async def _connect(self, user: Any) -> WebsocketCommunicator:
        communicator = WebsocketCommunicator(LiveTapeConsumer.as_asgi(), "/ws/cw/live/")
        communicator.scope["user"] = user
        return communicator

    @pytest.mark.django_db(transaction=True)
    async def test_relays_group_batches_to_socket(self):
        user = await sync_to_async(User.objects.create_user)(username="op", password="x")
        communicator = await self._connect(user)
        connected, _ = await communicator.connect()
        assert connected

        layer = get_channel_layer()
        payload = {"chars": [{"c": "K"}], "meta": {"tone_hz": 600}}
        await layer.group_send(
            live_group_name(user.pk), {"type": "cw.batch", "payload": payload}
        )
        assert await communicator.receive_json_from() == payload
        await communicator.disconnect()

    async def test_rejects_anonymous(self):
        communicator = await self._connect(AnonymousUser())
        connected, _ = await communicator.connect()
        assert not connected


@pytest.mark.asyncio
class TestLiveIngest:
    @pytest.mark.django_db(transaction=True)
    async def test_session_auth_relays_to_own_group(self, client):
        user = await sync_to_async(User.objects.create_user)(username="op", password="pw")
        await sync_to_async(client.force_login)(user)

        layer = get_channel_layer()
        await layer.group_add(live_group_name(user.pk), "probe-channel")

        payload = {"chars": [], "key_runs": [{"on": True, "t": 0.1, "ms": 60}], "meta": {}}
        response = await sync_to_async(client.post)(
            reverse("cw-live-ingest"), json.dumps(payload), content_type="application/json"
        )
        assert response.status_code == 200

        message = await layer.receive("probe-channel")
        assert message["type"] == "cw.batch"
        assert message["payload"] == payload

    @pytest.mark.django_db(transaction=True)
    async def test_bearer_token_relays_to_token_owner(self, client):
        from django.utils import timezone

        from apps.smallstack.models import APIToken

        user = await sync_to_async(User.objects.create_user)(username="op2", password="pw")
        _, raw_key = await sync_to_async(APIToken.create_token)(
            user, name="stream test", access_level="auth",
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        layer = get_channel_layer()
        await layer.group_add(live_group_name(user.pk), "probe-token")

        response = await sync_to_async(client.post)(
            reverse("cw-live-ingest"), json.dumps({"chars": [], "meta": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_key}",
        )
        assert response.status_code == 200
        message = await layer.receive("probe-token")
        assert message["type"] == "cw.batch"


@pytest.mark.django_db
class TestLiveIngestAuth:
    def test_anonymous_is_rejected(self, client):
        response = client.post(
            reverse("cw-live-ingest"), json.dumps({"chars": []}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_non_object_payload_rejected(self, client):
        user = User.objects.create_user(username="op3", password="pw")
        client.force_login(user)
        response = client.post(
            reverse("cw-live-ingest"), json.dumps([1, 2, 3]),
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestLivePage:
    def test_requires_login(self, client):
        response = client.get(reverse("cw-live"))
        assert response.status_code == 302

    def test_renders_with_stream_instructions(self, client):
        user = User.objects.create_user(username="op4", password="pw")
        client.force_login(user)
        response = client.get(reverse("cw-live"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "cw_monitor_live --stream op4" in content
        assert "/ws/cw/live/" in content
        assert 'id="cw-live-pill"' in content
