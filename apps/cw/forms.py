from __future__ import annotations

import datetime

from django import forms
from django.utils import timezone as djtz

from .models import QSO
from .services import RECORDING_SQUELCH_DB

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 MB — a half-hour practice MP3 fits


class PracticeDecodeForm(forms.Form):
    """Synthesize text to CW and decode it back — the no-hardware loop."""

    text = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={
            "rows": 3, "class": "vTextField",
            "placeholder": "CQ CQ DE N0CALL K",
        }),
    )
    wpm = forms.FloatField(min_value=5, max_value=60, initial=20)
    tone_hz = forms.FloatField(min_value=300, max_value=1200, initial=600, label="Tone (Hz)")
    snr_db = forms.FloatField(
        required=False, min_value=0, max_value=40, label="Add noise (SNR dB)",
        help_text="Leave blank for a clean signal",
    )


class RecordingDecodeForm(forms.Form):
    """Decode a recording off a receiver — WAV, MP3, FLAC, or OGG."""

    recording = forms.FileField(label="Recording (WAV, MP3, FLAC, OGG)")
    auto_tone = forms.BooleanField(
        required=False, initial=True, label="Auto-detect tone",
        help_text="Find the CW note from the spectrum — recommended for off-air files",
    )
    tone_hz = forms.FloatField(min_value=300, max_value=1200, initial=600, label="Tone (Hz)")
    squelch_db = forms.FloatField(
        required=False, min_value=0, max_value=12,
        initial=RECORDING_SQUELCH_DB, label="Squelch (dB)",
        help_text="Gate the key against band noise; 0 is off",
    )

    def clean_squelch_db(self) -> float:
        # An omitted slider means "use the default", not "no squelch" — only an
        # explicit 0 turns the gate off.
        value = self.cleaned_data.get("squelch_db")
        return RECORDING_SQUELCH_DB if value is None else value

    def clean_recording(self) -> object:
        f = self.cleaned_data["recording"]
        if f.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("File too large (30 MB max).")
        return f


class QSOForm(forms.ModelForm):
    """The logbook line as a form. Times are entered and displayed in UTC —
    the ham convention and what the ADIF export writes — regardless of the
    server's TIME_ZONE."""

    when = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": 60}),
        label="Time · UTC",
    )

    class Meta:
        model = QSO
        fields = [
            "call", "when", "freq_mhz", "mode", "rst_sent", "rst_rcvd",
            "name", "qth", "gridsquare", "country", "comment",
        ]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        instance: QSO | None = kwargs.get("instance")  # type: ignore[assignment]
        when = instance.when if instance and instance.pk else djtz.now()
        self.initial["when"] = when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")

    def clean_when(self) -> datetime.datetime:
        value = self.cleaned_data["when"]
        # Django parsed the naive input in the server TZ; the operator typed
        # UTC — keep the wall time, swap the zone.
        wall = djtz.localtime(value) if djtz.is_aware(value) else value
        return wall.replace(tzinfo=datetime.timezone.utc)

    def clean_call(self) -> str:
        return self.cleaned_data["call"].strip().upper()


class SendForm(forms.Form):
    """Compose a message and key it into audio."""

    text = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={
            "rows": 3, "class": "vTextField",
            "placeholder": "CQ CQ CQ DE N0CALL N0CALL K",
        }),
    )
    wpm = forms.FloatField(min_value=5, max_value=60, initial=20)
    tone_hz = forms.FloatField(min_value=300, max_value=1200, initial=600, label="Sidetone (Hz)")
