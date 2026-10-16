"""Authenticated encryption wrapper (Fernet: AES-128-CBC + HMAC-SHA256, from the `cryptography` library).

No custom cryptography. Keys are never included in exception messages or logs.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """Raised when ciphertext cannot be verified or the key is unusable. Message is safe to log."""


class SensitiveDataCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise CryptoError("Encryption key is not configured.")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise CryptoError("Encryption key is malformed.") from exc

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt_bytes(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise CryptoError("Ciphertext could not be verified.") from exc

    def encrypt_text(self, plaintext: str) -> str:
        return self.encrypt_bytes(plaintext.encode("utf-8")).decode("ascii")

    def decrypt_text(self, ciphertext: str) -> str:
        return self.decrypt_bytes(ciphertext.encode("ascii")).decode("utf-8")
