"""verify_pkce: S256 only, reject plain / empty."""

import base64
import hashlib

from apps.mcp.oauth import verify_pkce


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_correct_verifier_passes():
    v = "x" * 64
    assert verify_pkce(v, _challenge(v), "S256") is True


def test_wrong_verifier_fails():
    v = "x" * 64
    assert verify_pkce("y" * 64, _challenge(v), "S256") is False


def test_plain_rejected():
    v = "x" * 64
    assert verify_pkce(v, v, "plain") is False


def test_empty_inputs_rejected():
    assert verify_pkce("", "anything", "S256") is False
    assert verify_pkce("verifier", "", "S256") is False
    assert verify_pkce("", "", "S256") is False
