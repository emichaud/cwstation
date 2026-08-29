"""FM Radio tests — receiver lifecycle, validation, favourites, page access.

The lifecycle tests drive the REAL `rtl_fm` when an SDR dongle is plugged in,
and skip cleanly where there isn't one (CI, or the dongle unplugged). Audio
never reaches a sound card: `start(sink=...)` takes a fake, the same seam
`transmit.py` uses to test the PTT sequence with no radio attached.
"""
from __future__ import annotations

import json
import shutil
from contextlib import contextmanager

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import radiodaemon
from apps.cw.models import RadioStation
from apps.cw.radiodaemon import RadioError

User = get_user_model()

has_rtl = shutil.which("rtl_fm") is not None
needs_rtl = pytest.mark.skipif(not has_rtl, reason="rtl-sdr tools not installed")
has_dongle = has_rtl and bool(radiodaemon.list_devices())
needs_dongle = pytest.mark.skipif(not has_dongle, reason="no SDR dongle attached")


@contextmanager
def _fake_sink_cm(collected: list[bytes]):
    yield collected.append


def fake_sink(collected: list[bytes]):
    """A sink factory shaped like `_speaker_sink` but with no audio hardware."""
    def factory():
        return _fake_sink_cm(collected)
    return factory


@pytest.fixture(autouse=True)
def receiver_cleanup():
    yield
    radiodaemon.stop()


class TestValidation:
    """Nothing reaches the argv unvalidated — the frequency comes from a slider
    but the endpoint accepts arbitrary JSON."""

    @pytest.mark.parametrize("freq", [87.9, 108.1, 7.03, 0, -100, 1e9])
    def test_rejects_frequency_outside_the_fm_band(self, freq: float):
        with pytest.raises(RadioError, match="outside the FM broadcast band"):
            radiodaemon.start(freq, sink=fake_sink([]))

    @pytest.mark.parametrize("freq", ["nonsense", None, "100.3; rm -rf /"])
    def test_rejects_non_numeric_frequency(self, freq: object):
        with pytest.raises(RadioError, match="Not a frequency"):
            radiodaemon.start(freq, sink=fake_sink([]))  # type: ignore[arg-type]

    def test_rejects_negative_device_index(self):
        with pytest.raises(RadioError, match="device index"):
            radiodaemon.start(100.3, device_index=-1, sink=fake_sink([]))


class TestDeviceDiscovery:
    def test_list_devices_never_raises(self):
        """The page must render an empty state rather than a 500 when there's
        no hardware, no toolchain, or the dongle is busy."""
        assert isinstance(radiodaemon.list_devices(refresh=True), list)

    def test_status_reports_toolchain_presence(self):
        s = radiodaemon.status()
        assert set(s) >= {"running", "freq_mhz", "band", "rtl_fm_present", "sounddevice_present"}
        assert s["band"] == {"low": 88.0, "high": 108.0}

    @pytest.mark.parametrize("boom", [ImportError("no module"), OSError("PortAudio not found")])
    def test_missing_audio_stack_is_reported_not_raised(self, monkeypatch, boom):
        """`make test` keeps the live extra installed, so on a box without
        PortAudio the import fails with OSError rather than ImportError. Both
        must read as 'no speaker', not blow up the status endpoint."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "sounddevice":
                raise boom
            return real_import(name, *a, **kw)

        monkeypatch.delitem(__import__("sys").modules, "sounddevice", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert radiodaemon.rtl_status()["sounddevice_present"] is False

    def test_empty_scan_is_not_cached(self, monkeypatch):
        """A scan that fails because the dongle is momentarily busy must not
        strand the page on 'No SDR detected' until a manual rescan."""
        monkeypatch.setattr(radiodaemon, "_devices_cache", None)
        monkeypatch.setattr(radiodaemon.shutil, "which", lambda name: None)
        assert radiodaemon.list_devices(refresh=True) == []
        assert radiodaemon._devices_cache is None


@needs_dongle
class TestReceiverLifecycle:
    def test_start_pumps_audio_then_stops(self):
        chunks: list[bytes] = []
        state = radiodaemon.start(100.3, sink=fake_sink(chunks))
        assert state["running"] is True
        assert state["freq_mhz"] == 100.3
        # start() waits ~1s for the process to settle, so audio is already flowing
        assert chunks, "no PCM reached the sink"
        assert radiodaemon.stop()["running"] is False

    def test_second_start_is_refused_while_running(self):
        radiodaemon.start(100.3, sink=fake_sink([]))
        with pytest.raises(RadioError, match="already running"):
            radiodaemon.start(99.5, sink=fake_sink([]))


@pytest.mark.django_db
class TestStationsEndpoint:
    @pytest.fixture
    def client_logged(self, client):
        User.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        return client

    def _post(self, client, payload):
        return client.post(
            reverse("cw-radio-stations"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_create_and_list(self, client_logged):
        r = self._post(client_logged, {"name": "WXYZ", "freq_mhz": 100.3})
        assert r.status_code == 200
        listed = client_logged.get(reverse("cw-radio-stations")).json()
        stations = (listed.get("data") or listed)["stations"]
        assert [(s["name"], s["freq_mhz"]) for s in stations] == [("WXYZ", 100.3)]

    def test_duplicate_name_is_rejected(self, client_logged):
        self._post(client_logged, {"name": "WXYZ", "freq_mhz": 100.3})
        r = self._post(client_logged, {"name": "WXYZ", "freq_mhz": 101.1})
        assert r.status_code == 409

    def test_out_of_band_frequency_is_rejected(self, client_logged):
        r = self._post(client_logged, {"name": "Shortwave", "freq_mhz": 7.03})
        assert r.status_code == 400
        assert not RadioStation.objects.exists()

    def test_rename_and_delete(self, client_logged):
        created = self._post(client_logged, {"name": "WXYZ", "freq_mhz": 100.3}).json()
        pk = (created.get("data") or created)["id"]
        assert self._post(client_logged, {"id": pk, "name": "Jazz"}).status_code == 200
        assert RadioStation.objects.get(pk=pk).name == "Jazz"
        assert self._post(client_logged, {"id": pk, "delete": True}).status_code == 200
        assert not RadioStation.objects.filter(pk=pk).exists()

    def test_stations_are_per_operator(self, client_logged, client):
        """One operator's favourites must never appear in another's strip —
        the per-user scoping every CW model has."""
        self._post(client_logged, {"name": "Mine", "freq_mhz": 100.3})
        User.objects.create_user(username="other", password="pw")
        other = client
        other.login(username="other", password="pw")
        listed = other.get(reverse("cw-radio-stations")).json()
        assert (listed.get("data") or listed)["stations"] == []
        # ...and can't be reached by guessing the id
        pk = RadioStation.objects.get(name="Mine").pk
        assert self._post(other, {"id": pk, "delete": True}).status_code == 404
        assert RadioStation.objects.filter(pk=pk).exists()

    def test_anonymous_is_refused(self, client):
        assert client.get(reverse("cw-radio-stations")).status_code in (401, 403, 302)


@pytest.mark.django_db
class TestRadioPage:
    def test_renders_for_logged_in_operator(self, client):
        User.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        body = client.get(reverse("cw-radio")).content.decode()
        # the faceplate pieces the JS binds to
        for element_id in ("fm-chassis", "fm-freq", "fm-favs", "fm-freq-val",
                           "fm-listen", "fm-device-note"):
            assert f'id="{element_id}"' in body

    def test_anonymous_is_redirected(self, client):
        r = client.get(reverse("cw-radio"))
        assert r.status_code == 302 and "login" in r["Location"]


@pytest.mark.django_db
class TestControlEndpoint:
    @pytest.fixture
    def client_logged(self, client):
        User.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        return client

    def test_get_reports_receiver_state(self, client_logged):
        payload = client_logged.get(reverse("cw-radio-control")).json()
        data = payload.get("data") or payload
        assert "devices" in data and "running" in data
        assert json.dumps(data)  # the whole reply must be JSON-serialisable

    def test_bad_action_is_rejected(self, client_logged):
        r = client_logged.post(
            reverse("cw-radio-control"),
            data=json.dumps({"action": "explode"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_out_of_band_tune_is_refused(self, client_logged):
        r = client_logged.post(
            reverse("cw-radio-control"),
            data=json.dumps({"action": "tune", "freq_mhz": 7.03}),
            content_type="application/json",
        )
        assert r.status_code == 409
