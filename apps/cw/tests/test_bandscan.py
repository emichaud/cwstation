"""Antenna survey tests — scoring, guards, persistence, and the page.

The scoring maths is pure and runs on canned rtl_power CSV. The parts that need
a dongle are guarded the same way the radio tests are: they skip cleanly when
there's no hardware, so this suite is meaningful in CI and on a bare laptop.
"""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw import bandscan
from apps.cw.models import AntennaSurvey
from apps.cw.radiodaemon import RadioError

User = get_user_model()


def _csv(low_hz: float, step_hz: float, dbs: list[float]) -> str:
    body = ", ".join(str(d) for d in dbs)
    return f"2026-08-29, 16:00:00, {low_hz}, {low_hz + step_hz * len(dbs)}, {step_hz}, 64, {body}\n"


class TestScoring:
    """SNR — peak above the band's median floor — is what antennas are compared
    by, so its behaviour is pinned here."""

    def test_summarize_reports_floor_peak_and_snr(self):
        band = bandscan.BANDS_BY_KEY["noaa"]
        # floor about -30, one carrier at -5 → ~25 dB SNR
        bins = [(162.40 + i * 0.002, -30.0) for i in range(40)]
        bins[20] = (bins[20][0], -5.0)
        out = bandscan.summarize(band, bins)
        assert out["floor_db"] == pytest.approx(-30.0, abs=0.6)
        assert out["peak_db"] == pytest.approx(-5.0, abs=0.1)
        assert out["snr_db"] == pytest.approx(25.0, abs=0.6)
        assert out["peak_mhz"] == pytest.approx(bins[20][0], abs=1e-3)
        assert out["signals"] == 1

    def test_flat_band_scores_near_zero(self):
        """A dead band must read ~0, not a false positive — this is the whole
        difference between 'the antenna is bad' and 'nothing is transmitting'."""
        band = bandscan.BANDS_BY_KEY["10m_cw"]
        bins = [(28.0 + i * 0.001, -14.0 + (i % 3) * 0.1) for i in range(60)]
        out = bandscan.summarize(band, bins)
        assert out["snr_db"] < 1.0
        assert out["signals"] == 0

    def test_too_few_bins_is_no_data_not_a_crash(self):
        out = bandscan.summarize(bandscan.BANDS_BY_KEY["fm"], [(88.1, -20.0)])
        assert out["snr_db"] is None and out["floor_db"] is None

    def test_parse_clips_to_the_band(self):
        band = bandscan.BANDS_BY_KEY["noaa"]  # 162.40–162.56
        text = _csv(162_000_000, 100_000, [-30.0] * 12)  # 162.00 … 163.20
        bins = bandscan._parse_in_band(text, band)
        assert bins, "expected some bins inside the band"
        assert all(band.low_mhz <= mhz <= band.high_mhz for mhz, _ in bins)

    @pytest.mark.parametrize(
        "snr,expected",
        [(None, "no data"), (0.4, "nothing heard"), (5.0, "faint"),
         (12.0, "workable"), (26.0, "strong")],
    )
    def test_verdict_wording(self, snr, expected):
        assert bandscan.verdict(snr) == expected


class TestBandTable:
    def test_reference_bands_are_always_on_transmitters(self):
        """Reference bands are what make an antenna comparison valid; if one
        stops being always-on it should be demoted deliberately, not silently."""
        refs = {b.key for b in bandscan.BANDS if b.reference}
        assert refs == {"fm", "noaa", "10m_beacon"}

    def test_defaults_include_a_reference_band(self):
        default = set(bandscan.DEFAULT_BAND_KEYS)
        assert default & {b.key for b in bandscan.BANDS if b.reference}

    def test_every_band_is_a_valid_range(self):
        for b in bandscan.BANDS:
            assert b.low_mhz < b.high_mhz, b.key
            assert b.step_khz > 0, b.key


class TestStartGuards:
    def test_unnamed_antenna_is_refused(self):
        with pytest.raises(RadioError, match="Name the antenna"):
            bandscan.start(antenna="  ", band_keys=["fm"])

    def test_no_bands_is_refused(self):
        with pytest.raises(RadioError, match="at least one band"):
            bandscan.start(antenna="whip", band_keys=[])

    def test_unknown_band_keys_are_ignored(self):
        with pytest.raises(RadioError, match="at least one band"):
            bandscan.start(antenna="whip", band_keys=["not-a-band"])

    def test_refused_while_the_receiver_is_playing(self, monkeypatch):
        """The dongle does one thing at a time; a survey must not silently kill
        someone's audio."""
        from apps.cw import radiodaemon

        monkeypatch.setattr(radiodaemon, "status", lambda: {"running": True})
        with pytest.raises(RadioError, match="receiver is playing"):
            bandscan.start(antenna="whip", band_keys=["fm"])


@pytest.mark.django_db
class TestSurveyEndpoint:
    @pytest.fixture
    def client_logged(self, client):
        User.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        return client

    def test_get_lists_bands_and_defaults(self, client_logged):
        payload = client_logged.get(reverse("cw-survey-scan")).json()
        data = payload.get("data") or payload
        assert {b["key"] for b in data["bands"]} == set(bandscan.BANDS_BY_KEY)
        assert data["defaults"]["bands"] == bandscan.DEFAULT_BAND_KEYS
        assert json.dumps(data)  # whole reply must be JSON-serialisable

    def test_bad_action_is_rejected(self, client_logged):
        r = client_logged.post(
            reverse("cw-survey-scan"),
            data=json.dumps({"action": "detonate"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_unnamed_antenna_returns_409(self, client_logged):
        r = client_logged.post(
            reverse("cw-survey-scan"),
            data=json.dumps({"action": "start", "antenna": "", "bands": ["fm"]}),
            content_type="application/json",
        )
        assert r.status_code == 409

    def test_surveys_are_per_operator(self, client_logged, client):
        mine = AntennaSurvey.objects.create(
            user=User.objects.get(username="op"), antenna="dipole",
            gain_db=40.0, results=[{"key": "fm", "snr_db": 25.0}],
        )
        User.objects.create_user(username="other", password="pw")
        other = client
        other.login(username="other", password="pw")
        payload = other.get(reverse("cw-survey-scan")).json()
        assert (payload.get("data") or payload)["surveys"] == []
        # and can't be deleted by guessing the id
        r = other.post(
            reverse("cw-survey-scan"),
            data=json.dumps({"action": "delete", "id": mine.pk}),
            content_type="application/json",
        )
        assert r.status_code == 404
        assert AntennaSurvey.objects.filter(pk=mine.pk).exists()

    def test_delete_removes_own_survey(self, client_logged):
        survey = AntennaSurvey.objects.create(
            user=User.objects.get(username="op"), antenna="loop",
            gain_db=40.0, results=[],
        )
        r = client_logged.post(
            reverse("cw-survey-scan"),
            data=json.dumps({"action": "delete", "id": survey.pk}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert not AntennaSurvey.objects.filter(pk=survey.pk).exists()

    def test_anonymous_is_refused(self, client):
        assert client.get(reverse("cw-survey-scan")).status_code in (401, 403, 302)


@pytest.mark.django_db
class TestSurveyModel:
    def test_score_for_reads_a_band(self):
        user = User.objects.create_user(username="op2", password="pw")
        survey = AntennaSurvey.objects.create(
            user=user, antenna="whip", gain_db=40.0,
            results=[{"key": "fm", "snr_db": 26.0}, {"key": "noaa", "snr_db": None}],
        )
        assert survey.score_for("fm") == 26.0
        assert survey.score_for("noaa") is None   # swept, heard nothing
        assert survey.score_for("2m") is None     # not swept at all


@pytest.mark.django_db
class TestSurveyPage:
    def test_renders_for_logged_in_operator(self, client):
        User.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        body = client.get(reverse("cw-survey")).content.decode()
        for element_id in ("sv-antenna", "sv-bands", "sv-gain", "sv-run", "sv-matrix"):
            assert f'id="{element_id}"' in body

    def test_anonymous_is_redirected(self, client):
        r = client.get(reverse("cw-survey"))
        assert r.status_code == 302 and "login" in r["Location"]
