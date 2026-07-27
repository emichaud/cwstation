"""Bridge scripted payloads (REST JSON, MCP args, ``sc`` CLI flags) into ModelForm data.

Django ModelForms speak HTML-form semantics: every value is a string, an
unchecked checkbox means False, and an omitted field is "blank". Scripted
surfaces speak data semantics: JSON arrays are arrays, booleans are booleans,
and an omitted field means "use the model default" (create) or "keep the
current value" (partial update). Historically each scripted surface hand-rolled
this translation by stringifying everything into a QueryDict, which produced
three real bugs:

- JSONField round-trip: ``model_to_dict`` yields the *Python* value (``['*']``),
  ``str()`` turns it into ``"['*']"`` (single quotes), and re-validation fails
  with "Enter a valid JSON." — so a partial update touching any *other* field
  broke on a populated JSONField.
- Native JSON rejection: a REST/MCP client sending ``"event_filter": ["*"]``
  (exactly what GET returns) had the list split into a multi-value string,
  failing the same way. Only double-encoded strings worked.
- Silent default loss: an omitted BooleanField became False (the unchecked-
  checkbox mechanic) even when the model default is True — which silently
  turned off ``require_signature``-style security defaults — and an omitted
  CharField with a model default errored "This field is required."

This module owns the translation once, for every scripted surface. The key
insight is that bound forms accept a plain dict of **native Python values**:
``forms.JSONField.to_python`` passes lists/dicts through, ``BooleanField``
handles real booleans, ``DateTimeField`` accepts datetimes. So no stringifying
is needed — just merge instance values / model defaults underneath the incoming
payload and hand the dict to the form.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from django import forms
from django.forms.models import model_to_dict
from django.http import QueryDict


def _native(value: Any, form_field=None) -> Any:
    """Normalize one incoming value for bound-form consumption.

    JSONField-destined non-string values are JSON-encoded: empty containers
    (``[]``/``{}``) sit in ``Field.empty_values``, so passed natively they
    collapse to ``None`` in ``to_python`` and violate NOT NULL — the string
    form ``"[]"`` round-trips correctly, and non-empty natives gain nothing
    over it. Model instances collapse to their pk (``model_to_dict`` returns
    instances for M2M); everything else passes through — form fields'
    ``to_python`` handle native lists/dicts/bools/datetimes correctly.
    """
    if isinstance(form_field, forms.JSONField):
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return [v.pk if hasattr(v, "pk") else v for v in value]
    return value


def merge_form_payload(
    form_class,
    incoming: Mapping[str, Any],
    *,
    instance=None,
    instance_fields: list[str] | None = None,
    fill_defaults: bool = False,
) -> dict[str, Any]:
    """Build form data for a scripted create/update from a native payload.

    - ``instance`` (partial update): unspecified fields keep their current
      values, seeded via ``model_to_dict`` limited to ``instance_fields``.
    - ``fill_defaults`` (create): fields the form knows about that are absent
      from ``incoming`` fall back to the **model field default** when one
      exists — so ``BooleanField(default=True)`` stays True and a CharField
      default satisfies "required" exactly like an ORM ``.create()`` would.
    - ``incoming`` wins over both. A ``QueryDict`` payload is flattened with
      multi-value keys preserved as lists.
    """
    data: dict[str, Any] = {}
    base_fields = form_class.base_fields

    if instance is not None:
        for key, value in model_to_dict(instance, fields=instance_fields).items():
            data[key] = _native(value, base_fields.get(key))

    if fill_defaults:
        model = form_class._meta.model
        for name in base_fields:
            if name in data:
                continue
            try:
                model_field = model._meta.get_field(name)
            except Exception:
                continue
            if model_field.has_default():
                default = model_field.get_default()
                if default is not None:
                    data[name] = _native(default, base_fields.get(name))

    if isinstance(incoming, QueryDict):
        for key in incoming:
            values = incoming.getlist(key)
            data[key] = values if len(values) > 1 else values[0]
    else:
        for key, value in incoming.items():
            data[key] = _native(value, base_fields.get(key))

    return data
