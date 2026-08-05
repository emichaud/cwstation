from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models
from django.urls import reverse


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
