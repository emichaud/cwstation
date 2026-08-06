"""Encryption-at-rest for third-party service credentials (QRZ, eQSL).

Fernet (AES-128-CBC + HMAC) with a per-install key held in a file OUTSIDE
the database — `.cw_credentials_key` next to the project (0600, gitignored),
auto-created on first use. Django's SECRET_KEY can't be the key source: in
development it regenerates per process, which would brick stored secrets on
every restart.

Values are stored as `enc:<token>`. Legacy plaintext values (no prefix)
still decrypt as-is and get re-encrypted on the next save — a transparent
migration path.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:"
_KEY_FILENAME = ".cw_credentials_key"
_fernet: Fernet | None = None


def _key_path() -> str:
    return str(getattr(settings, "CW_CREDENTIALS_KEY_FILE", None)
               or os.path.join(settings.BASE_DIR, _KEY_FILENAME))


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    path = _key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    _fernet = Fernet(key)
    return _fernet


def reset_cache() -> None:
    """Testing hook: forget the cached Fernet (e.g. after switching key files)."""
    global _fernet
    _fernet = None


def encrypt(value: str) -> str:
    if not value:
        return ""
    return PREFIX + _get_fernet().encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    """Decrypt a stored value; legacy plaintext passes through unchanged."""
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        return stored  # pre-encryption row — readable, re-encrypted on next save
    try:
        return _get_fernet().decrypt(stored[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # key file changed/lost — treat as unset, never crash
