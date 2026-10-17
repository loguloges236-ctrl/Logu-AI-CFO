"""Authenticated encryption wrapper (Fernet: AES-128-CBC + HMAC-SHA256, from the `cryptography` library).

No custom cryptography. Keys are never included in exception messages or logs.

Key rotation: the primary key encrypts all new data. Previous keys are accepted for decryption only, so data written
before a rotation stays readable until it is re-encrypted with `rotate_bytes` (see docs/KEY_ROTATION.md).
"""

from __future__ import annotations

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoError(Exception):
    """Raised when ciphertext cannot be verified or the key is unusable. Message is safe to log."""


class SensitiveDataCipher:
    def __init__(self, key: str, previous_keys: Sequence[str] = ()) -> None:
        if not key:
            raise CryptoError("Encryption key is not configured.")
        try:
            self._primary = Fernet(key.encode("ascii"))
            previous = [Fernet(old.encode("ascii")) for old in previous_keys if old]
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise CryptoError("Encryption key is malformed.") from exc
        self._fernet = MultiFernet([self._primary, *previous])

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt_bytes(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise CryptoError("Ciphertext could not be verified.") from exc

    def is_current(self, ciphertext: bytes) -> bool:
        """True when the token verifies with the primary key (it does not need re-encryption)."""
        try:
            self._primary.decrypt(ciphertext)
        except InvalidToken:
            return False
        return True

    def rotate_bytes(self, ciphertext: bytes) -> bytes:
        """Re-encrypt a token under the primary key. Fails if no configured key can verify it."""
        try:
            return self._fernet.rotate(ciphertext)
        except InvalidToken as exc:
            raise CryptoError("Ciphertext could not be verified.") from exc

    def encrypt_text(self, plaintext: str) -> str:
        return self.encrypt_bytes(plaintext.encode("utf-8")).decode("ascii")

    def decrypt_text(self, ciphertext: str) -> str:
        return self.decrypt_bytes(ciphertext.encode("ascii")).decode("utf-8")
