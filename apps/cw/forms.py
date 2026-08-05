from __future__ import annotations

from django import forms

MAX_WAV_BYTES = 20 * 1024 * 1024  # 20 MB — minutes of 8 kHz mono, plenty


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


class WavDecodeForm(forms.Form):
    """Decode a WAV recorded off a receiver (or exported from GQRX)."""

    wav = forms.FileField(label="WAV file")
    tone_hz = forms.FloatField(min_value=300, max_value=1200, initial=600, label="Tone (Hz)")

    def clean_wav(self) -> object:
        f = self.cleaned_data["wav"]
        if f.size > MAX_WAV_BYTES:
            raise forms.ValidationError("File too large (20 MB max).")
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
