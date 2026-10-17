from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.errors import APIError
from app.security.crypto import CryptoError, SensitiveDataCipher
from app.services.interfaces import DisabledNotificationProvider, NotificationProvider
from app.storage.local import LocalFileStorage
from app.storage.scanner import MalwareScanner, build_scanner


@lru_cache
def _storage() -> LocalFileStorage:
    return LocalFileStorage(get_settings().upload_storage_path)


def get_storage() -> LocalFileStorage:
    return _storage()


def get_cipher() -> SensitiveDataCipher:
    try:
        settings = get_settings()
        previous = [k.strip() for k in settings.data_encryption_key_previous.split(",") if k.strip()]
        return SensitiveDataCipher(settings.data_encryption_key, previous)
    except CryptoError:
        raise APIError(
            503,
            "document_storage_unavailable",
            "Document storage is not configured. Contact the administrator.",
        ) from None


def get_scanner() -> MalwareScanner:
    return build_scanner()


def get_notifier() -> NotificationProvider:
    return DisabledNotificationProvider()
