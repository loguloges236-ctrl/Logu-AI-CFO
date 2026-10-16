"""Keyed hashing and secret generation (stdlib only).

Session tokens, CSRF tokens, device identifiers, reset tokens and IP addresses are stored only as
HMAC-SHA256 digests under a server-side key. Each digest is domain-separated by a purpose label,
so a digest produced for one purpose cannot be replayed for another.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid

MIN_KEY_BYTES = 32
_STORAGE_KEY_PATTERN = re.compile(r"[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}")


class KeyedDigester:
    def __init__(self, key: bytes) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise ValueError("HMAC key must be at least 32 bytes")
        self._key = key

    def digest(self, purpose: str, value: str) -> str:
        message = f"{purpose}\x1f{value}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def is_storage_key(value: str) -> bool:
    return _STORAGE_KEY_PATTERN.fullmatch(value) is not None


def new_storage_key(owner_id: uuid.UUID, document_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """Opaque, non-guessable relative key. Contains no filename or user-controlled text."""
    return f"{owner_id.hex}/{document_id.hex}/{file_id.hex}"
