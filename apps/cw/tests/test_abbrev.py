"""Tutor-mode abbreviation glossing."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.cw.abbrev import LOOKUP, gloss

User = get_user_model()


class TestGloss:
    def test_finds_qcodes_and_words_in_order(self):
        out = gloss("CQ CQ DE W1AW QTH IS BOSTON HW? 73")
        tokens = [g["token"] for g in out]
        assert tokens == ["CQ", "DE", "QTH", "HW", "73"]  # deduped, first-seen order
        assert out[0]["meaning"] == LOOKUP["CQ"]

    def test_strips_punctuation_and_uppercases(self):
        out = gloss("hw? tu, es 73.")
        assert [g["token"] for g in out] == ["HW", "TU", "ES", "73"]

    def test_keeps_sign_tokens(self):
        out = gloss("W1AW = K5TR + SK")
        tokens = [g["token"] for g in out]
        assert "=" in tokens and "+" in tokens and "SK" in tokens

    def test_ignores_plain_words_and_callsigns(self):
        assert gloss("HELLO WORLD N0CALL BOSTON") == []

    def test_empty(self):
        assert gloss("") == []

    def test_every_lookup_value_is_nonempty(self):
        assert all(v.strip() for v in LOOKUP.values())


@pytest.mark.django_db
class TestAbbrevEndpoint:
    def test_returns_dictionary(self, client):
        user = User.objects.create_user(username="op", password="pw")
        client.force_login(user)
        payload = client.get(reverse("cw-abbrev")).json()
        data = payload.get("data") or payload
        assert data["lookup"]["CQ"] == LOOKUP["CQ"]
        assert "QTH" in data["lookup"]

    def test_requires_auth(self, client):
        assert client.get(reverse("cw-abbrev")).status_code == 401
