"""Band-simulator + AFC + level-control tests — the no-radio workbench."""
from __future__ import annotations

import json

import numpy as np
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw.engine import CWConfig, CWDecoder, synthesize_cw
from apps.cw.engine.simulate import SimulatedBandSource
from apps.cw.models import CWSimControl

User = get_user_model()


def run_decoder(source: SimulatedBandSource, cfg: CWConfig) -> tuple[str, CWDecoder]:
    dec = CWDecoder(source.sample_rate, cfg)
    for blk in source.blocks():
        dec.process(blk)
    return dec.finalize().text, dec


class TestSimulatedBand:
    def test_deterministic_per_seed(self):
        a = SimulatedBandSource(seed=7, duration_s=15)
        b = SimulatedBandSource(seed=7, duration_s=15)
        blocks_a = np.concatenate(list(a.blocks()))
        blocks_b = np.concatenate(list(b.blocks()))
        assert np.array_equal(blocks_a, blocks_b)
        assert a.truth == b.truth

    def test_stations_are_logged_with_pitch_and_speed(self):
        src = SimulatedBandSource(seed=42, duration_s=30)
        list(src.blocks())
        assert src.log, "no stations transmitted in 30s"
        for tx in src.log:
            assert 450 <= tx.tone_hz <= 950
            assert 14 <= tx.wpm <= 26

    def test_paused_signals_gives_pure_noise(self):
        src = SimulatedBandSource(seed=1, duration_s=10)
        src.paused_signals = True
        list(src.blocks())
        assert src.log == []


class TestAFC:
    def test_afc_locks_onto_offset_station(self):
        # station at 812 Hz, decoder starts at 600 — AFC must re-tune
        src = SimulatedBandSource(seed=42, duration_s=14, noise_level=0.05)
        text, dec = run_decoder(src, CWConfig(tone_hz=600, afc=True, squelch_db=3.0))
        assert "W1AW" in text
        assert dec.result.tone_hz == pytest.approx(src.log[0].tone_hz, abs=25)

    def test_afc_chases_successive_stations(self):
        src = SimulatedBandSource(seed=42, duration_s=40, noise_level=0.05)
        text, _ = run_decoder(src, CWConfig(tone_hz=600, afc=True, squelch_db=3.0))
        # three stations at 812/487/730 Hz for this seed — all must be copied
        assert "CQ CQ CQ DE W1AW W1AW K" in text
        assert "TU 5NN 73 <SK>" in text
        assert "QRL? DE N0CALL" in text

    def test_afc_off_never_retunes(self):
        # Control case: without AFC the decoder stays parked at 600 Hz. (With
        # 4 ms blocks the DFT bins are 250 Hz wide, so an off-pitch station can
        # still be partially heard through a neighbor bin — for this seed the
        # 812 Hz copy degrades to garble mid-message. AFC's value is exact
        # centering, which the lock test above asserts.)
        src = SimulatedBandSource(seed=42, duration_s=14, noise_level=0.05)
        _, dec = run_decoder(src, CWConfig(tone_hz=600, afc=False, squelch_db=3.0))
        assert dec.result.tone_hz == 600

    def test_afc_does_not_chase_pure_noise(self):
        src = SimulatedBandSource(seed=3, duration_s=8, noise_level=0.2)
        src.paused_signals = True
        _, dec = run_decoder(src, CWConfig(tone_hz=600, afc=True, squelch_db=3.0))
        assert dec.result.tone_hz == 600  # prominence gate held


class TestLevelControls:
    def test_squelch_silences_noise_only_band(self):
        src = SimulatedBandSource(seed=5, duration_s=20, noise_level=0.25)
        src.paused_signals = True
        text, _ = run_decoder(src, CWConfig(tone_hz=600, squelch_db=6.0))
        assert text == ""

    def test_squelched_decoder_still_copies_real_signal(self):
        src = SimulatedBandSource(seed=42, duration_s=14, noise_level=0.05)
        text, _ = run_decoder(src, CWConfig(tone_hz=812, squelch_db=6.0))
        assert "W1AW" in text

    def test_input_gain_recovers_weak_signal_telemetry(self):
        # gain scales the envelope (and thus the absolute magnitudes the
        # trackers see); decode of a quiet clean signal works at 10x
        r = synthesize_cw("PARIS", wpm=20, tone_hz=600, sample_rate=8000, amplitude=0.02)
        dec = CWDecoder(8000, CWConfig(tone_hz=600, input_gain=10.0))
        dec.process(r.audio)
        assert dec.finalize().text == "PARIS"


@pytest.mark.django_db
class TestSimControlEndpoint:
    def test_requires_auth(self, client):
        assert client.get(reverse("cw-sim-control")).status_code == 401

    def test_get_creates_defaults(self, client):
        user = User.objects.create_user(username="op", password="pw")
        client.force_login(user)
        response = client.get(reverse("cw-sim-control"))
        assert response.status_code == 200
        data = response.json()["data"] if "data" in response.json() else response.json()
        assert data["afc"] is True
        assert data["squelch_db"] == 3.0

    def test_post_partial_update_and_clamping(self, client):
        user = User.objects.create_user(username="op2", password="pw")
        client.force_login(user)
        response = client.post(
            reverse("cw-sim-control"),
            json.dumps({"noise_level": 99, "afc": False}),
            content_type="application/json",
        )
        assert response.status_code == 200
        control = CWSimControl.objects.get(user=user)
        assert control.noise_level == 0.5  # clamped
        assert control.afc is False
        assert control.squelch_db == 3.0  # untouched

    def test_rejects_non_numeric(self, client):
        user = User.objects.create_user(username="op3", password="pw")
        client.force_login(user)
        response = client.post(
            reverse("cw-sim-control"),
            json.dumps({"noise_level": "loud"}),
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestSimulatorPage:
    def test_requires_login(self, client):
        assert client.get(reverse("cw-sim")).status_code == 302

    def test_renders_knobs_and_command(self, client):
        user = User.objects.create_user(username="op4", password="pw")
        client.force_login(user)
        content = client.get(reverse("cw-sim")).content.decode()
        assert "cw_simulate --stream op4" in content
        assert 'data-field="squelch_db"' in content
        assert 'data-field="noise_level"' in content
        assert 'id="k-afc"' in content
