from __future__ import annotations

from django import forms

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

    def clean_recording(self) -> object:
        f = self.cleaned_data["recording"]
        if f.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("File too large (30 MB max).")
        return f


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
