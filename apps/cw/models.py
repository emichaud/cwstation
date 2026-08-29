from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.timezone import now as django_timezone_now

if TYPE_CHECKING:
    # Annotation-only; see apps/cw/apitypes.py for why the concrete model
    # is named rather than AbstractBaseUser.
    from apps.accounts.models import User



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
    callsign = models.CharField(
        max_length=20, blank=True,
        help_text="Your station callsign. Fills {mycall} in send macros and the "
                  "ADIF STATION_CALLSIGN. Blank = fall back to your username.",
    )
    # Default keying settings — the send popup and the decode keyer start here.
    send_wpm = models.PositiveSmallIntegerField(default=20)
    send_tone_hz = models.PositiveSmallIntegerField(default=600)
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


def _rig_photo_path(instance: CWRigPhoto, filename: str) -> str:
    """Per-operator media path: cw/rig_photos/user_<id>/<model>.<ext>."""
    import os

    ext = os.path.splitext(filename)[1].lower() or ".png"
    return f"cw/rig_photos/user_{instance.user_id}/{instance.rig_model}{ext}"


class CWRigPhoto(models.Model):
    """An operator's own photo of their radio, shown on the Rig Setup page in
    place of the built-in illustration.

    This is *user-supplied* content, stored per operator under MEDIA_ROOT — the
    product itself ships no manufacturer photos (copyright). Each operator drops
    in a picture from their own library; the effect is a rig that looks like the
    real thing, with the copyright resting entirely with the person who uploaded
    it. One photo per (operator, Hamlib model)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_rig_photos"
    )
    rig_model = models.PositiveIntegerField(help_text="Hamlib rig model number (rigctl -l)")
    image = models.ImageField(upload_to=_rig_photo_path)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CW Rig Photo"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "rig_model"], name="uniq_rig_photo_per_user_model"
            )
        ]

    def __str__(self) -> str:
        return f"photo of rig {self.rig_model} for {self.user}"

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        # remove the underlying file, not just the row
        self.image.delete(save=False)
        return super().delete(*args, **kwargs)


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
    def seed_defaults(cls, user: User) -> None:
        """Give a new operator the standard keyer memories (idempotent)."""
        if cls.objects.filter(user=user).exists():
            return
        CWMacro.objects.bulk_create([
            CWMacro(user=user, name=name, text=text, order=i)
            for i, (name, text) in enumerate(DEFAULT_MACROS)
        ])


class CWVariable(models.Model):
    """A named value the operator can drop into any message as `{name}`.

    Where a macro is a whole message template, a variable is a single reusable
    value — your rig, antenna, name, QTH, power. Define `rig = KW4420` once and
    `{rig}` expands to `KW4420` everywhere: when a macro is inserted and when the
    composed message is sent. The reserved names {mycall}, {call}, {rst} are
    filled by the station and can't be shadowed here.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cw_variables"
    )
    name = models.SlugField(
        max_length=24, help_text="The {tag} — lowercase, no spaces (e.g. rig, ant, qth)"
    )
    value = models.CharField(max_length=200, help_text="What {tag} expands to")
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_variable_per_user")
        ]
        verbose_name = "CW Variable"

    def __str__(self) -> str:
        return f"{{{self.name}}} = {self.value}"


# Reserved placeholder names the station fills — operators can't redefine them.
RESERVED_VARIABLE_NAMES = frozenset({"mycall", "call", "rst"})


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
        choices=[
            ("manual", "Manual"), ("session", "From session"),
            ("reply", "On-air reply"), ("import", "ADIF import"),
        ],
    )
    eqsl_sent_at = models.DateTimeField(null=True, blank=True)
    qrz_sent_at = models.DateTimeField(null=True, blank=True)
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


class _CredentialMixin(models.Model):
    """Shared shape for third-party service credentials. Passwords are
    encrypted at rest (Fernet, key file outside the DB — see fieldcrypto.py)
    and only ever handled through set_password/get_password."""

    username = models.CharField(max_length=64, blank=True)
    password = models.CharField(max_length=512, blank=True)  # enc:<token>
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def set_password(self, raw: str) -> None:
        from .fieldcrypto import encrypt

        self.password = encrypt(raw)

    def get_password(self) -> str:
        from .fieldcrypto import decrypt, encrypt

        raw = decrypt(self.password)
        # transparent upgrade of any legacy plaintext row
        if raw and not self.password.startswith("enc:"):
            self.password = encrypt(raw)
            self.save(update_fields=["password", "updated_at"])
        return raw


class QRZProfile(_CredentialMixin):
    """QRZ.com credentials: the XML-API login (lookups) and, separately,
    a logbook.qrz.com API key (log import/export) — QRZ issues those
    per-logbook, distinct from the account password. Both encrypted."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="qrz_profile"
    )
    session_key = models.CharField(max_length=64, blank=True)
    logbook_key = models.CharField(max_length=512, blank=True)  # enc:<token>

    def set_logbook_key(self, raw: str) -> None:
        from .fieldcrypto import encrypt

        self.logbook_key = encrypt(raw)

    def get_logbook_key(self) -> str:
        from .fieldcrypto import decrypt

        return decrypt(self.logbook_key)

    def __str__(self) -> str:
        return f"QRZ credentials for {self.user}"


class EQSLProfile(_CredentialMixin):
    """eQSL.cc credentials for log upload (ImportADIF)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="eqsl_profile"
    )

    def __str__(self) -> str:
        return f"eQSL credentials for {self.user}"


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


class RadioStation(models.Model):
    """A saved broadcast station — the FM Radio page's favourites strip.

    Same shape as `CWMacro`: per-user, ordered, one name per operator. Recalling
    one just retunes the receiver, so nothing here is validated against what's
    actually on the air — an operator may save a frequency that's dead where
    they are.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="radio_stations"
    )
    name = models.CharField(
        max_length=32, help_text="What to call it on the strip (e.g. WXYZ, Jazz)"
    )
    freq_mhz = models.FloatField(help_text="Frequency in MHz (FM broadcast: 88–108)")
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="unique_radio_station_per_user"
            )
        ]
        verbose_name = "Radio Station"

    def __str__(self) -> str:
        return f"{self.name} ({self.freq_mhz:g} MHz)"


class AntennaSurvey(models.Model):
    """One band sweep, labelled by the antenna it was taken with.

    The point of storing these is comparison: same bands, same gain, different
    antenna. `gain_db` is recorded rather than assumed because two runs taken at
    different gains aren't comparable and the page has to be able to say so.
    `results` holds one scored row per band (see `bandscan.summarize`).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="antenna_surveys"
    )
    antenna = models.CharField(
        max_length=60, help_text="What was connected — 'stock whip', 'dipole', 'loop'"
    )
    gain_db = models.FloatField(default=40.0, help_text="Tuner gain, pinned so runs compare")
    device = models.CharField(
        max_length=120, blank=True,
        help_text="Which SDR took the run — two dongles aren't comparable",
    )
    results = models.JSONField(default=list, help_text="Per-band scores from the sweep")
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Antenna Survey"

    def __str__(self) -> str:
        return f"{self.antenna} — {len(self.results)} bands"

    def score_for(self, band_key: str) -> float | None:
        """SNR for one band, or None if this survey didn't sweep it."""
        for row in self.results:
            if row.get("key") == band_key:
                value = row.get("snr_db")
                return float(value) if value is not None else None
        return None
