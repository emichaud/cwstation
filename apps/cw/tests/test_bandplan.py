"""Band plan tests — the chart is a reference an operator might key up on, so
the data's internal consistency is what's pinned here.

These can't verify the FCC rules themselves (only the FCC can), but they do
catch the mistakes that actually happen when editing a table by hand: a segment
that escapes its band, a class given more than the class above it, a bar whose
blocks don't add up.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from apps.cw import bandplan

pytestmark = pytest.mark.django_db


class TestBandTable:
    def test_every_band_is_a_valid_range(self):
        for b in bandplan.BANDS:
            assert b.low_mhz < b.high_mhz, b.key
            assert b.width_mhz > 0, b.key

    def test_bands_are_ordered_low_to_high(self):
        lows = [b.low_mhz for b in bandplan.BANDS]
        assert lows == sorted(lows)

    def test_every_segment_falls_inside_its_band(self):
        """The mistake a hand-edited chart actually makes: a typo'd digit puts
        a segment outside the band it belongs to."""
        for band in bandplan.BANDS:
            for p in band.privileges:
                for name, span in (("cw_data", p.cw_data), ("phone", p.phone)):
                    if span is None:
                        continue
                    low, high = span
                    assert low < high, f"{band.key}/{p.license}/{name} inverted"
                    assert band.low_mhz <= low <= band.high_mhz, \
                        f"{band.key}/{p.license}/{name} starts outside the band"
                    assert band.low_mhz <= high <= band.high_mhz, \
                        f"{band.key}/{p.license}/{name} ends outside the band"

    def test_no_class_gets_more_than_the_class_above_it(self):
        """Privileges nest: an Extra can key anywhere a General can. A chart
        that violates that is telling someone they may transmit where they
        may not."""
        for band in bandplan.BANDS:
            extra = band.privilege_for("Extra")
            for lower in ("Advanced", "General", "Technician"):
                p = band.privilege_for(lower)
                for name, span, wider in (
                    ("cw_data", p.cw_data, extra.cw_data),
                    ("phone", p.phone, extra.phone),
                ):
                    if span is None:
                        continue
                    assert wider is not None, f"{band.key}: {lower} has {name}, Extra doesn't"
                    assert wider[0] <= span[0] and span[1] <= wider[1], \
                        f"{band.key}: {lower}'s {name} escapes Extra's"

    def test_thirty_metres_has_no_phone_for_anyone(self):
        """A real rule people get wrong, and the reason the chart exists."""
        band = bandplan.BANDS_BY_KEY["30m"]
        assert all(p.phone is None for p in band.privileges)

    def test_ten_metres_is_the_only_hf_phone_a_technician_gets(self):
        with_phone = [
            b.key for b in bandplan.HF_BANDS
            if b.privilege_for("Technician").phone is not None
        ]
        assert with_phone == ["10m"]

    def test_technician_has_full_privileges_above_fifty_megahertz(self):
        for band in bandplan.VHF_BANDS:
            tech, extra = band.privilege_for("Technician"), band.privilege_for("Extra")
            assert tech.cw_data == extra.cw_data, band.key
            assert tech.phone == extra.phone, band.key

    def test_tuner_floor_agrees_with_the_receiver(self):
        """bandplan duplicates the constant to stay dependency-free; if the two
        drift, the chart tells operators the wrong thing about their hardware."""
        from apps.cw.radiodaemon import TUNER_FLOOR_MHZ

        assert bandplan.TUNER_FLOOR_MHZ == TUNER_FLOOR_MHZ

    def test_upconverter_flag_matches_the_floor(self):
        for b in bandplan.BANDS:
            assert b.needs_upconverter == (b.high_mhz < bandplan.TUNER_FLOOR_MHZ), b.key


class TestChartGeometry:
    """The proportional bars are computed, so they can be wrong silently."""

    def test_zones_stay_inside_the_bar(self):
        for band in bandplan.BANDS:
            for z in band.zones:
                assert z.left_pct >= -0.01, f"{band.key} zone starts before the bar"
                assert z.left_pct + z.width_pct <= 100.01, \
                    f"{band.key} zone runs past the end of the bar"
                assert z.width_pct > 0, f"{band.key} zone has no width"

    def test_ticks_stay_inside_the_bar(self):
        for band in bandplan.BANDS:
            for t in band.entry_ticks:
                assert 0 < float(t["left_pct"]) <= 100, band.key

    def test_coinciding_ticks_are_merged_into_one_label(self):
        """General and Technician both start at 3.525 on 80 m. Drawn as two
        ticks they overprint into an unreadable smear."""
        ticks = bandplan.BANDS_BY_KEY["80m"].entry_ticks
        assert len(ticks) == 1
        assert ticks[0]["licenses"] == "General · Technician"
        assert ticks[0]["label"] == "3.525"

    def test_a_band_with_no_subband_split_draws_one_block(self):
        """160 m gives every class the whole band for both modes. Two zones
        would stack, and the phone block would paint over the CW one — a bar
        reading 'phone only' for a band that is mostly CW at the bottom."""
        zones = bandplan.BANDS_BY_KEY["160m"].zones
        assert len(zones) == 1
        assert zones[0].kind == "both"
        assert zones[0].width_pct == 100.0

    def test_no_tick_where_a_class_starts_at_the_band_edge(self):
        """On 160 m every class starts at 1.800 — a tick there would just
        repeat the edge label."""
        assert bandplan.BANDS_BY_KEY["160m"].entry_ticks == []

    def test_a_full_band_fills_its_bar(self):
        """40 m's Extra allocation is contiguous end to end, so its two zones
        must tile the whole bar with no gap and no overlap."""
        band = bandplan.BANDS_BY_KEY["40m"]
        cw, phone = band.zones
        assert cw.left_pct == pytest.approx(0.0, abs=0.01)
        assert cw.left_pct + cw.width_pct == pytest.approx(phone.left_pct, abs=0.01)
        assert phone.left_pct + phone.width_pct == pytest.approx(100.0, abs=0.01)

    def test_channelised_bands_have_no_bar(self):
        """60 m is five channels, not a span — drawing it as a bar would imply
        you may work anywhere between them."""
        band = bandplan.BANDS_BY_KEY["60m"]
        assert band.channels
        assert band.zones == []

    def test_labels_round_trip_the_numbers(self):
        band = bandplan.BANDS_BY_KEY["40m"]
        rows = {r["license"]: r for r in band.rows}
        assert rows["Extra"]["cw_data"] == "7.000–7.125"
        assert rows["General"]["phone"] == "7.175–7.300"
        assert rows["Technician"]["phone"] == ""
        assert rows["Technician"]["none"] == ""   # it does have CW


class TestCallingFrequencies:
    def test_sorted_low_to_high(self):
        khz = [c.khz for c in bandplan.cw_calling_table()]
        assert khz == sorted(khz)

    def test_every_calling_frequency_lands_in_its_own_band(self):
        """A watering hole outside the band it's listed under would send
        someone to the wrong place on the dial."""
        for band in bandplan.BANDS:
            for c in band.calling:
                mhz = c.khz / 1000
                assert band.low_mhz <= mhz <= band.high_mhz, f"{c.label} {c.khz} in {band.key}"

    def test_every_hf_calling_frequency_is_in_a_cw_segment(self):
        """These are CW watering holes; one sitting in the phone segment would
        be an invitation to key up somewhere CW isn't allowed."""
        for band in bandplan.HF_BANDS:
            cw = band.privilege_for("Extra").cw_data
            if cw is None or band.channels:
                continue
            for c in band.calling:
                mhz = c.khz / 1000
                assert cw[0] <= mhz <= cw[1], \
                    f"{band.key}: {c.label} at {c.mhz} is outside the CW segment"


class TestBandPlanPage:
    def test_renders_for_a_logged_in_operator(self, client, django_user_model):
        django_user_model.objects.create_user(username="op", password="pw")
        client.login(username="op", password="pw")
        body = client.get(reverse("cw-bandplan")).content.decode()
        assert "Band Chart" in body
        for expected in ("40 m", "7.000–7.125", "30 m", "SKCC"):
            assert expected in body, expected

    def test_anonymous_is_redirected(self, client):
        r = client.get(reverse("cw-bandplan"))
        assert r.status_code == 302 and "login" in r["Location"]

    def test_the_template_hard_codes_no_colours(self):
        """The AI-built-page failure mode: a hex literal looks right on one
        palette and brown on the other four. Asserted against the template
        source, not the rendered page — the base template legitimately emits
        the palette definitions themselves as hex.
        """
        import re
        from pathlib import Path

        import apps.cw

        source = (
            Path(apps.cw.__file__).parent / "templates" / "cw" / "bandplan.html"
        ).read_text()
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", source)
