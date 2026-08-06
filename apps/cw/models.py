from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.timezone import now as django_timezone_now


class CWSession(models.Model):
    """One decode or send pass through the CW engine.

    `telemetry` holds the full session dict (`engine.export.session_from_result`)
    so the monitor can replay the pass — key runs, envelope, per-character
    WPM/SNR/confidence. Audio is never stored: synthesized sessions are
    deterministic and are regenerated on demand for playback.
    """

    class Direction(models.TextChoices):
        RECEIVED = "rx", "Received"
        SENT = "tx", "Sent"

    class Source(models.TextChoices):
        SYNTH = "synth", "Practice (synthesized)"
        WAV = "wav", "Recording upload"
        TEXT = "text", "Composed message"
        LIVE = "live", "Live monitor"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_sessions"
    )
    direction = models.CharField(max_length=2, choices=Direction.choices)
    source = models.CharField(max_length=8, choices=Source.choices)
    text = models.TextField(help_text="Decoded copy (rx) or composed message (tx)")
    truth = models.TextField(blank=True, help_text="Ground-truth text for practice decodes")
    wpm = models.FloatField(default=0.0)
    tone_hz = models.FloatField(default=600.0)
    snr_db = models.FloatField(null=True, blank=True)
    callsigns = models.JSONField(default=list, blank=True)
    telemetry = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "CW Session"
        verbose_name_plural = "CW Sessions"

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.wpm:.0f} wpm: {self.text[:40]}"

    def get_absolute_url(self) -> str:
        return reverse("cw/sessions-detail", args=[self.pk])

    @property
    def can_replay(self) -> bool:
        """The monitor can animate this session (telemetry was captured)."""
        telemetry: dict[str, Any] = self.telemetry or {}
        return bool(telemetry.get("key_runs"))

    @property
    def has_audio(self) -> bool:
        """Audio can be regenerated deterministically (not for WAV uploads)."""
        return self.source in (self.Source.SYNTH, self.Source.TEXT)

    @property
    def accuracy(self) -> float | None:
        """Character accuracy vs ground truth, for practice decodes."""
        if not self.truth:
            return None
        want = self.truth.upper()
        got = self.text
        if not want:
            return None
        return sum(1 for a, b in zip(got, want) if a == b) / len(want)


class CWRig(models.Model):
    """The operator's rig connection — a Hamlib `rigctld` daemon.

    rigctld runs on the machine wired to the radio (often the same box as
    this server). When enabled and reachable, the live page shows rig state
    and the send sheet can key the transmitter: CAT PTT on → play the keyed
    audio out the chosen sound device into the rig → PTT off.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_rig"
    )
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=200, default="127.0.0.1")
    port = models.PositiveIntegerField(default=4532)
    use_ptt = models.BooleanField(
        default=True, help_text="Key PTT via CAT. Off = rely on the rig's VOX."
    )
    # Rig Setup launcher: what the managed rigctld was last started with
    rig_model = models.PositiveIntegerField(
        null=True, blank=True, help_text="Hamlib rig model number (rigctl -l)"
    )
    serial_port = models.CharField(max_length=200, blank=True)
    baud = models.PositiveIntegerField(default=115200)
    audio_output = models.CharField(
        max_length=200, blank=True,
        help_text="Output sound device name/index for TX audio (blank = system default)",
    )
    ptt_lead_ms = models.PositiveIntegerField(
        default=150, help_text="Delay between PTT-on and audio so the first dit isn't clipped"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CW Rig"

    def __str__(self) -> str:
        return f"rigctld {self.host}:{self.port} for {self.user}"


DEFAULT_MACROS: list[tuple[str, str]] = [
    ("cq", "CQ CQ CQ DE {mycall} {mycall} K"),
    ("qrz", "QRZ? DE {mycall} K"),
    ("rst", "{call} DE {mycall} UR RST {rst} {rst} BK"),
    ("73", "73 TU ES GL {call} DE {mycall} <SK>"),
    ("agn", "AGN AGN PSE {call} DE {mycall} BK"),
    ("qth", "QTH IS {qth} {qth} BK"),
]


class CWMacro(models.Model):
    """A message memory — the CW equivalent of a contest keyer's F-key.

    Triggered from the Send composer by typing /name (slash palette) or
    clicking its keycap chip. `{placeholders}` expand at insert time:
    {mycall} fills from the operator, {call} from the reply context; anything
    unknown is selected in the composer for quick typing.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_macros"
    )
    name = models.SlugField(
        max_length=24, help_text="The /command — lowercase, no spaces (e.g. cq, 73, rst)"
    )
    text = models.CharField(
        max_length=280,
        help_text="Message to key. Placeholders: {call} {mycall} {rst} — or your own.",
    )
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_macro_per_user")
        ]
        verbose_name = "CW Macro"

    def __str__(self) -> str:
        return f"/{self.name}"

    @classmethod
    def seed_defaults(cls, user: object) -> None:
        """Give a new operator the standard keyer memories (idempotent)."""
        if cls.objects.filter(user=user).exists():
            return
        cls.objects.bulk_create(
            cls(user=user, name=name, text=text, order=i)
            for i, (name, text) in enumerate(DEFAULT_MACROS)
        )


class QSO(models.Model):
    """One logged contact.

    QSOs stay linked to the session they were copied/keyed in, so every log
    row can replay its tape. Callbook fields (name/QTH/grid/country) fill
    from QRZ when the operator has credentials, else stay editable by hand.
    Times are stored aware and exported in UTC — the ADIF convention.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="qsos"
    )
    call = models.CharField(max_length=20, db_index=True)
    when = models.DateTimeField(default=django_timezone_now)
    freq_mhz = models.FloatField(null=True, blank=True)
    band = models.CharField(max_length=8, blank=True)
    mode = models.CharField(max_length=12, default="CW")
    rst_sent = models.CharField(max_length=8, blank=True, default="599")
    rst_rcvd = models.CharField(max_length=8, blank=True)
    name = models.CharField(max_length=120, blank=True)
    qth = models.CharField(max_length=120, blank=True)
    gridsquare = models.CharField(max_length=8, blank=True)
    country = models.CharField(max_length=64, blank=True)
    comment = models.TextField(blank=True)
    session = models.ForeignKey(
        CWSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="qsos"
    )
    source = models.CharField(
        max_length=8, default="manual",
        choices=[("manual", "Manual"), ("session", "From session"), ("reply", "On-air reply")],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-when"]
        verbose_name = "QSO"
        verbose_name_plural = "QSOs"

    def __str__(self) -> str:
        return f"{self.call} · {self.when:%Y-%m-%d %H:%M} · {self.mode}"

    def get_absolute_url(self) -> str:
        return reverse("cw/log-update", args=[self.pk])

    @property
    def qrz_url(self) -> str:
        return f"https://www.qrz.com/db/{self.call}"


class QRZProfile(models.Model):
    """QRZ.com XML-API credentials (requires a QRZ XML subscription).

    Stored per operator on this self-hosted instance, like the rig config.
    The session key is cached and refreshed on timeout.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="qrz_profile"
    )
    username = models.CharField(max_length=64, blank=True)
    password = models.CharField(max_length=128, blank=True)
    session_key = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"QRZ credentials for {self.user}"


class CWSimControl(models.Model):
    """The operator's live knobs for a running simulation (or live monitor).

    The Simulator page writes these; the `cw_simulate` process polls the row
    about twice a second and applies changes between audio blocks — the DB is
    the one medium both processes already share.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_sim_control"
    )
    noise_level = models.FloatField(default=0.08, help_text="Band static level (0–0.5)")
    input_gain = models.FloatField(default=1.0, help_text="Input level multiplier (0.1–10)")
    squelch_db = models.FloatField(
        default=3.0, help_text="SNR gate in dB; below it the key can't open (0 = off)"
    )
    afc = models.BooleanField(
        default=True, help_text="Automatic frequency control — chase the strongest carrier"
    )
    paused_signals = models.BooleanField(
        default=False, help_text="Static only — stop scheduling stations"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CW Simulator Control"

    def __str__(self) -> str:
        return f"sim controls for {self.user}"

    def clamped(self) -> "CWSimControl":
        """Return self with values clamped to safe ranges (defends the
        decoder from wild slider values)."""
        self.noise_level = min(max(self.noise_level, 0.0), 0.5)
        self.input_gain = min(max(self.input_gain, 0.1), 10.0)
        self.squelch_db = min(max(self.squelch_db, 0.0), 12.0)
        return self
