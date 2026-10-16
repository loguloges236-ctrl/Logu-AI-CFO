from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_MIN_LENGTH = 14
PASSWORD_MAX_LENGTH = 256

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("dummy-password-for-timing-equalisation")


def burn_verification_time(password: str) -> None:
    """Spend the same Argon2 work when no account matches, so response timing does not reveal existence."""
    verify_password(_dummy_hash(), password)
