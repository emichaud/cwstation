"""The US amateur band plan, as data — the frequency chart's single source.

Two different things get conflated on every band chart, so they're kept apart
here:

- **Subband edges are law.** FCC Part 97.301 (who may transmit where) and 97.305
  (which emissions are allowed there). Those are `Band.privileges`.
- **Calling frequencies are convention.** QRP, SKCC and FISTS watering holes are
  voluntary IARU/club band-plan practice, not regulation — nobody is violating
  anything by working 7.031. Those are `Band.calling`, and the page says so.

Region 2 (the Americas) only. Regions 1 and 3 differ, most sharply on 40 m.
This is a bench reference, not a legal authority: the FCC rules and the ARRL
band chart are.

Ranges are stored as **numbers**, not display strings, so the proportional bars
on the chart page can be computed from the same values the tables print — and so
a test can assert every segment actually falls inside its band's edges.

Django-free and import-light on purpose — the same rule `engine/` follows — so
the data can be tested without a settings module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A plain RTL-SDR stick's tuner has no signal path below this; everything under
# it needs direct sampling or an upconverter. Mirrors radiodaemon.TUNER_FLOOR_MHZ
# (duplicated rather than imported to keep this module dependency-free — a test
# asserts the two agree, so they can't drift).
TUNER_FLOOR_MHZ = 24.0

# The license classes, strongest first. Advanced is closed to new issues but
# still held, so it stays on the chart.
LICENSE_CLASSES = ("Extra", "Advanced", "General", "Technician")

Span = tuple[float, float]


def _fmt(mhz: float, decimals: int) -> str:
    return f"{mhz:.{decimals}f}"


def _span_label(span: Span | None, decimals: int) -> str:
    if span is None:
        return ""
    return f"{_fmt(span[0], decimals)}–{_fmt(span[1], decimals)}"


@dataclass(frozen=True)
class Zone:
    """One coloured block on a band's proportional bar."""

    kind: str          # "cw" | "phone"
    label: str
    left_pct: float
    width_pct: float


@dataclass(frozen=True)
class Privilege:
    """What one license class may do on one band.

    `None` means "nothing here" — a Technician has no 20 m at all, and 30 m has
    no phone for anyone. Rendered as an em dash.
    """

    license: str
    cw_data: Span | None = None
    phone: Span | None = None

    @property
    def has_access(self) -> bool:
        return self.cw_data is not None or self.phone is not None


@dataclass(frozen=True)
class Calling:
    """A CW calling frequency. Convention, not regulation."""

    khz: float
    label: str
    band: str = ""

    @property
    def mhz(self) -> str:
        return f"{self.khz / 1000:.3f}"


@dataclass(frozen=True)
class Band:
    key: str
    name: str
    low_mhz: float
    high_mhz: float
    privileges: tuple[Privilege, ...]
    decimals: int = 3
    power: str = "1500 W PEP"
    note: str = ""
    calling: tuple[Calling, ...] = field(default_factory=tuple)
    vhf: bool = False
    channels: tuple[float, ...] = field(default_factory=tuple)

    # ── labels ────────────────────────────────────────────────────────────
    @property
    def range_label(self) -> str:
        return f"{_fmt(self.low_mhz, self.decimals)}–{_fmt(self.high_mhz, self.decimals)} MHz"

    @property
    def width_mhz(self) -> float:
        return self.high_mhz - self.low_mhz

    @property
    def rows(self) -> list[dict[str, str]]:
        """One table row per license class, pre-formatted for the template."""
        return [
            {
                "license": p.license,
                "cw_data": _span_label(p.cw_data, self.decimals),
                "phone": _span_label(p.phone, self.decimals),
                "none": "" if p.has_access else "no privileges on this band",
            }
            for p in self.privileges
        ]

    # ── the chart bar ─────────────────────────────────────────────────────
    @property
    def zones(self) -> list[Zone]:
        """The Extra-class allocation as proportional blocks.

        Extra because it's the widest — the other classes are drawn as tick
        marks inside it, which is how the ARRL chart reads and why the bar is
        worth having at all.
        """
        if self.channels:
            return []
        extra = self.privilege_for("Extra")
        if extra.cw_data is not None and extra.cw_data == extra.phone:
            # 160 m has no FCC subband split — CW and phone are the same span.
            # Drawn as two zones they stack, and the phone block paints over
            # the CW one, so the bar reads "phone only" for a band that is
            # mostly CW at the bottom. One honest block instead.
            return [Zone("both", "CW & phone — no FCC split", 0.0, 100.0)]
        out: list[Zone] = []
        for kind, span in (("cw", extra.cw_data), ("phone", extra.phone)):
            if span is None:
                continue
            left = (span[0] - self.low_mhz) / self.width_mhz * 100
            width = (span[1] - span[0]) / self.width_mhz * 100
            out.append(Zone(
                kind=kind,
                label="CW / data" if kind == "cw" else "phone / image",
                left_pct=round(left, 3),
                width_pct=round(width, 3),
            ))
        return out

    @property
    def entry_ticks(self) -> list[dict[str, str | float]]:
        """Where the lower classes' privileges start, as bar positions.

        Two corrections the first draft needed, both visible on the page:

        - **Merged when they coincide.** General and Technician usually start
          at the same frequency (3.525, 21.025). Two labels at one position
          overprint into an unreadable smear.
        - **Dropped at the band edge.** When a class starts where the band
          does, the tick repeats the edge label and says nothing.
        """
        by_freq: dict[float, list[str]] = {}
        for license_class in ("General", "Technician"):
            p = self.privilege_for(license_class)
            span = p.cw_data or p.phone
            if span is None or span[0] <= self.low_mhz:
                continue
            by_freq.setdefault(span[0], []).append(license_class)
        return [
            {
                "left_pct": round((freq - self.low_mhz) / self.width_mhz * 100, 3),
                "licenses": " · ".join(names),
                "label": _fmt(freq, self.decimals),
            }
            for freq, names in sorted(by_freq.items())
        ]

    # ── receiver reach ────────────────────────────────────────────────────
    @property
    def needs_upconverter(self) -> bool:
        """True when a plain RTL-SDR stick can't hear this band at all."""
        return self.high_mhz < TUNER_FLOOR_MHZ

    def privilege_for(self, license_class: str) -> Privilege:
        for p in self.privileges:
            if p.license == license_class:
                return p
        return Privilege(license_class)

    @property
    def technician_access(self) -> str:
        """Plain-language entry-class summary — the single most-asked question
        a band chart answers."""
        tech = self.privilege_for("Technician")
        if not tech.has_access:
            return ""
        bits = []
        if tech.cw_data:
            bits.append(f"{_span_label(tech.cw_data, self.decimals)} CW/data")
        if tech.phone:
            bits.append(f"{_span_label(tech.phone, self.decimals)} phone")
        return " · ".join(bits)


def _all(cw_data: Span, phone: Span | None = None) -> tuple[Privilege, ...]:
    """Same privileges for every class — the VHF/UHF case at 50 MHz and up."""
    return tuple(Privilege(c, cw_data, phone) for c in LICENSE_CLASSES)


HF_BANDS: list[Band] = [
    Band(
        "160m", "160 m", 1.800, 2.000,
        privileges=(
            Privilege("Extra", (1.800, 2.000), (1.800, 2.000)),
            Privilege("Advanced", (1.800, 2.000), (1.800, 2.000)),
            Privilege("General", (1.800, 2.000), (1.800, 2.000)),
            Privilege("Technician"),
        ),
        note="No FCC subbands here — the CW/phone split is band-plan "
             "convention, CW at the bottom and phone above about 1.843. A night "
             "band; summer static crashes make it hard work.",
        calling=(Calling(1810, "QRP", "160 m"), Calling(1820, "SKCC", "160 m")),
    ),
    Band(
        "80m", "80 / 75 m", 3.500, 4.000,
        privileges=(
            Privilege("Extra", (3.500, 3.600), (3.600, 4.000)),
            Privilege("Advanced", (3.525, 3.600), (3.700, 4.000)),
            Privilege("General", (3.525, 3.600), (3.800, 4.000)),
            Privilege("Technician", (3.525, 3.600)),
        ),
        note="A Technician's first HF band, CW only. Regional at night, "
             "short-range by day.",
        calling=(
            Calling(3550, "SKCC", "80 m"), Calling(3558, "FISTS", "80 m"),
            Calling(3560, "QRP", "80 m"),
        ),
    ),
    Band(
        "60m", "60 m", 5.3305, 5.4065,
        privileges=(
            Privilege("Extra", (5.3305, 5.4065), (5.3305, 5.4065)),
            Privilege("Advanced", (5.3305, 5.4065), (5.3305, 5.4065)),
            Privilege("General", (5.3305, 5.4065), (5.3305, 5.4065)),
            Privilege("Technician"),
        ),
        decimals=4,
        power="100 W PEP ERP",
        channels=(5.3320, 5.3480, 5.3585, 5.3730, 5.4050),
        note="Channelised, not a band. Five channels, 2.8 kHz maximum "
             "bandwidth, USB only for phone — and CW is sent on the channel "
             "centre frequency, not anywhere in a segment.",
    ),
    Band(
        "40m", "40 m", 7.000, 7.300,
        privileges=(
            Privilege("Extra", (7.000, 7.125), (7.125, 7.300)),
            Privilege("Advanced", (7.025, 7.125), (7.125, 7.300)),
            Privilege("General", (7.025, 7.125), (7.175, 7.300)),
            Privilege("Technician", (7.025, 7.125)),
        ),
        note="The busiest CW band in the world and the most dependable one, "
             "day or night. Regions 1 and 3 stop at 7.200 — listen before you "
             "call DX up there.",
        calling=(
            Calling(7030, "QRP", "40 m"), Calling(7055, "SKCC", "40 m"),
            Calling(7058, "FISTS", "40 m"),
        ),
    ),
    Band(
        "30m", "30 m", 10.100, 10.150,
        privileges=(
            Privilege("Extra", (10.100, 10.150)),
            Privilege("Advanced", (10.100, 10.150)),
            Privilege("General", (10.100, 10.150)),
            Privilege("Technician"),
        ),
        power="200 W PEP",
        note="CW and data only — no phone, for any license class. A WARC band, "
             "so contests stay off it: quiet when 40 and 20 are a wall of noise.",
        calling=(
            Calling(10106, "QRP", "30 m"), Calling(10118, "FISTS", "30 m"),
            Calling(10120, "SKCC", "30 m"),
        ),
    ),
    Band(
        "20m", "20 m", 14.000, 14.350,
        privileges=(
            Privilege("Extra", (14.000, 14.150), (14.150, 14.350)),
            Privilege("Advanced", (14.025, 14.150), (14.175, 14.350)),
            Privilege("General", (14.025, 14.150), (14.225, 14.350)),
            Privilege("Technician"),
        ),
        note="The DX workhorse — open somewhere for most daylight hours. No "
             "Technician privileges at all.",
        calling=(
            Calling(14050, "SKCC", "20 m"), Calling(14058, "FISTS", "20 m"),
            Calling(14060, "QRP", "20 m"),
        ),
    ),
    Band(
        "17m", "17 m", 18.068, 18.168,
        privileges=(
            Privilege("Extra", (18.068, 18.110), (18.110, 18.168)),
            Privilege("Advanced", (18.068, 18.110), (18.110, 18.168)),
            Privilege("General", (18.068, 18.110), (18.110, 18.168)),
            Privilege("Technician"),
        ),
        note="WARC band — no contests. Narrow, civil, and often open when "
             "20 m is crowded.",
        calling=(
            Calling(18080, "SKCC", "17 m"), Calling(18085, "FISTS", "17 m"),
            Calling(18096, "QRP", "17 m"),
        ),
    ),
    Band(
        "15m", "15 m", 21.000, 21.450,
        privileges=(
            Privilege("Extra", (21.000, 21.200), (21.200, 21.450)),
            Privilege("Advanced", (21.025, 21.200), (21.225, 21.450)),
            Privilege("General", (21.025, 21.200), (21.275, 21.450)),
            Privilege("Technician", (21.025, 21.200)),
        ),
        note="Daytime DX that rises and falls with the solar cycle. A "
             "Technician's second CW band.",
        calling=(
            Calling(21050, "SKCC", "15 m"), Calling(21058, "FISTS", "15 m"),
            Calling(21060, "QRP", "15 m"),
        ),
    ),
    Band(
        "12m", "12 m", 24.890, 24.990,
        privileges=(
            Privilege("Extra", (24.890, 24.930), (24.930, 24.990)),
            Privilege("Advanced", (24.890, 24.930), (24.930, 24.990)),
            Privilege("General", (24.890, 24.930), (24.930, 24.990)),
            Privilege("Technician"),
        ),
        note="WARC band. Sits right at a bare RTL stick's tuner floor — the "
             "lowest band on this chart a plain dongle can even try to hear.",
        calling=(
            Calling(24906, "QRP", "12 m"), Calling(24910, "SKCC", "12 m"),
            Calling(24918, "FISTS", "12 m"),
        ),
    ),
    Band(
        "10m", "10 m", 28.000, 29.700,
        privileges=(
            Privilege("Extra", (28.000, 28.300), (28.300, 29.700)),
            Privilege("Advanced", (28.000, 28.300), (28.300, 29.700)),
            Privilege("General", (28.000, 28.300), (28.300, 29.700)),
            Privilege("Technician", (28.000, 28.300), (28.300, 28.500)),
        ),
        note="The one HF band where a Technician gets phone. Flat at solar "
             "minimum, worldwide on a dipole at maximum. The IBP beacons live "
             "around 28.2 and are the best propagation check there is.",
        calling=(
            Calling(28050, "SKCC", "10 m"), Calling(28058, "FISTS", "10 m"),
            Calling(28060, "QRP", "10 m"),
        ),
    ),
]

VHF_BANDS: list[Band] = [
    Band(
        "6m", "6 m", 50.0, 54.0, privileges=_all((50.0, 54.0), (50.1, 54.0)),
        decimals=1, vhf=True,
        note="“The magic band” — dead for weeks, then sporadic-E "
             "opens it coast to coast. 50.0–50.1 is CW only.",
        calling=(Calling(50090, "CW calling", "6 m"),),
    ),
    Band(
        "2m", "2 m", 144.0, 148.0, privileges=_all((144.0, 148.0), (144.1, 148.0)),
        decimals=1, vhf=True,
        note="Mostly repeaters and FM simplex, but the bottom 100 kHz is CW "
             "only and carries real weak-signal work.",
        calling=(Calling(144050, "CW calling", "2 m"),),
    ),
    Band(
        "1.25m", "1.25 m", 222.0, 225.0, privileges=_all((222.0, 225.0), (222.0, 225.0)),
        decimals=1, vhf=True,
        note="A US-only allocation. Quiet, and radios for it are scarce.",
    ),
    Band(
        "70cm", "70 cm", 420.0, 450.0, privileges=_all((420.0, 450.0), (420.0, 450.0)),
        decimals=1, vhf=True,
        note="Shared with government radiolocation — power and use are "
             "restricted in some areas. Heavy repeater and satellite activity.",
        calling=(Calling(432100, "CW / SSB calling", "70 cm"),),
    ),
    Band(
        "33cm", "33 cm", 902.0, 928.0, privileges=_all((902.0, 928.0), (902.0, 928.0)),
        decimals=1, vhf=True,
        note="Shares the ISM band with every unlicensed gadget in the house.",
    ),
    Band(
        "23cm", "23 cm", 1240.0, 1300.0, privileges=_all((1240.0, 1300.0), (1240.0, 1300.0)),
        decimals=1, vhf=True,
        note="The top of what a common RTL-SDR stick can tune.",
    ),
]

BANDS: list[Band] = HF_BANDS + VHF_BANDS
BANDS_BY_KEY = {b.key: b for b in BANDS}


def cw_calling_table() -> list[Calling]:
    """Every CW watering hole, low to high — the 'where do I listen' answer."""
    return sorted((c for b in BANDS for c in b.calling), key=lambda c: c.khz)


__all__ = [
    "BANDS", "BANDS_BY_KEY", "HF_BANDS", "LICENSE_CLASSES", "TUNER_FLOOR_MHZ",
    "VHF_BANDS", "Band", "Calling", "Privilege", "Zone", "cw_calling_table",
]
