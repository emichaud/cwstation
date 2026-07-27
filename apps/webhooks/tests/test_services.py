"""Unit tests for signing, the SSRF guard, and backoff (no DB needed)."""

from __future__ import annotations

from django.test import override_settings

from apps.webhooks import services


def test_sign_verify_roundtrip():
    secret = "s3cr3t"
    body = b'{"hello": "world"}'
    header = services.signature_header_value(secret, body)
    assert header.startswith("sha256=")
    # verify accepts both the prefixed header and the bare digest
    assert services.verify(secret, body, header)
    assert services.verify(secret, body, header.split("=", 1)[1])


def test_verify_rejects_bad_signature():
    body = b"payload"
    assert not services.verify("secret", body, "sha256=deadbeef")
    assert not services.verify("secret", body, "")
    assert not services.verify("secret", body, services.signature_header_value("other", body))


def test_backoff_clamps_to_last_entry():
    with override_settings(SMALLSTACK_WEBHOOK_BACKOFF=[10, 20, 30]):
        assert services.backoff_seconds(1) == 10
        assert services.backoff_seconds(2) == 20
        assert services.backoff_seconds(3) == 30
        assert services.backoff_seconds(4) == 30  # clamped
        assert services.backoff_seconds(99) == 30


@override_settings(SMALLSTACK_WEBHOOK_ALLOW_PRIVATE=False)
def test_url_guard_blocks_loopback():
    ok, reason = services.url_is_allowed("http://127.0.0.1:9000/hook")
    assert not ok
    assert "private" in reason.lower() or "loopback" in reason.lower()


@override_settings(SMALLSTACK_WEBHOOK_ALLOW_PRIVATE=True)
def test_url_guard_allows_loopback_when_private():
    ok, _ = services.url_is_allowed("http://127.0.0.1:9000/hook")
    assert ok


def test_url_guard_rejects_non_http_scheme():
    ok, reason = services.url_is_allowed("ftp://example.com/x")
    assert not ok
    assert "http" in reason.lower()


@override_settings(SMALLSTACK_WEBHOOK_ALLOWLIST=["example.com"])
def test_url_guard_enforces_allowlist():
    ok, reason = services.url_is_allowed("https://evil.test/hook")
    assert not ok
    assert "allowlist" in reason.lower()
    # a host under the allowlisted suffix passes the allowlist check
    ok2, _ = services.url_is_allowed("https://hooks.example.com/x")
    # (may still fail the private-IP resolve step depending on DNS, but must not
    # be rejected for the allowlist reason)
    assert ok2 or "allowlist" not in (_ or "")
