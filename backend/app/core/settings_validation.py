"""Pure configuration validation.

Kept free of framework imports so it can be unit-tested without installing the web stack.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath

_PRODUCTION_LIKE = frozenset({"staging", "production"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "testserver"})  # noqa: S104 - a rejected host name, not a bind address
_WEB_DIRECTORY_NAMES = frozenset({"public", "static", ".next", "out"})
MIN_HMAC_KEY_LENGTH = 32
MIN_BOOTSTRAP_TOKEN_LENGTH = 32


def is_valid_fernet_key(key: str) -> bool:
    """A Fernet key is 32 random bytes encoded as URL-safe base64."""
    try:
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    return len(raw) == 32


def _is_within(
    path: PurePosixPath | PureWindowsPath, root: PurePosixPath | PureWindowsPath
) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_variants(value: object) -> tuple[PurePosixPath, PureWindowsPath]:
    """Return the POSIX and Windows interpretations of a configured path.

    Configuration can be validated on a developer workstation that uses a
    different operating system than the eventual deployment host. Do not use
    the running host's ``Path`` class to decide whether the deployment path is
    absolute.
    """
    path = str(value or "")
    return PurePosixPath(path), PureWindowsPath(path)


def is_absolute_upload_storage_path(value: object) -> bool:
    """Return whether *value* is absolute in a supported deployment path syntax."""
    return any(path.is_absolute() for path in _path_variants(value))


def validate_settings(
    values: Mapping[str, object], *, validate_upload_path_absolute: bool = True
) -> list[str]:
    """Return a list of human-readable configuration errors (empty when valid).

    Secret values are never echoed in error messages.
    """
    errors: list[str] = []
    environment = str(values["environment"])
    production_like = environment in _PRODUCTION_LIKE

    database_url = str(values.get("database_url") or "")
    if not database_url.startswith("postgresql"):
        errors.append("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://...)")

    for name in ("ai_provider", "notification_provider"):
        if str(values.get(name)) != "disabled":
            errors.append(f"{name.upper()} must be 'disabled': no provider is implemented yet")

    encryption_key = str(values.get("data_encryption_key") or "")
    if encryption_key and not is_valid_fernet_key(encryption_key):
        errors.append("DATA_ENCRYPTION_KEY must be a Fernet key (32 bytes, URL-safe base64)")
    for previous in [k.strip() for k in str(values.get("data_encryption_key_previous") or "").split(",") if k.strip()]:
        if not is_valid_fernet_key(previous):
            errors.append("DATA_ENCRYPTION_KEY_PREVIOUS entries must each be a Fernet key")
            break

    hmac_key = str(values.get("security_hmac_key") or "")
    if hmac_key and len(hmac_key) < MIN_HMAC_KEY_LENGTH:
        errors.append(f"SECURITY_HMAC_KEY must be at least {MIN_HMAC_KEY_LENGTH} characters")

    if bool(values.get("enable_bootstrap")):
        if production_like:
            errors.append("ENABLE_BOOTSTRAP must be false in staging/production")
        if len(str(values.get("bootstrap_token") or "")) < MIN_BOOTSTRAP_TOKEN_LENGTH:
            errors.append(
                f"BOOTSTRAP_TOKEN must be at least {MIN_BOOTSTRAP_TOKEN_LENGTH} characters "
                "when bootstrap is enabled"
            )

    for item in values.get("trusted_proxy_networks") or []:
        try:
            ipaddress.ip_network(str(item), strict=False)
        except ValueError:
            errors.append("TRUSTED_PROXY_NETWORKS contains an invalid CIDR network")

    internal_hosts = [str(host).strip().lower() for host in values.get("internal_api_hosts") or []]
    for host in internal_hosts:
        if not host or "." in host or ":" in host or "*" in host or "/" in host:
            errors.append("INTERNAL_API_HOSTS must be single-label service names (for example: backend)")
            break
        if host in _LOCAL_HOSTS:
            errors.append("INTERNAL_API_HOSTS must not contain loopback names")
            break

    hosts = [str(host).strip().lower() for host in values.get("trusted_hosts") or []]
    origins = [str(origin).strip() for origin in values.get("frontend_origins") or []]

    if production_like:
        if not bool(values.get("force_https")):
            errors.append("FORCE_HTTPS must be true in staging/production")
        if not bool(values.get("cookie_secure")):
            errors.append("COOKIE_SECURE must be true in staging/production")
        if not hosts:
            errors.append("TRUSTED_HOSTS is required in staging/production")
        if any(host in _LOCAL_HOSTS or host == "*" for host in hosts):
            errors.append("TRUSTED_HOSTS must list only the public hostname(s) in production")
        if not hmac_key:
            errors.append("SECURITY_HMAC_KEY is required in staging/production")
        elif hmac_key.upper().startswith("CHANGE-ME"):
            errors.append("SECURITY_HMAC_KEY still contains the .env.example placeholder")
        if not encryption_key:
            errors.append("DATA_ENCRYPTION_KEY is required in staging/production")
        for origin in origins:
            if not origin.startswith("https://") or "*" in origin:
                errors.append("FRONTEND_ORIGINS must be exact https:// origins in production")
        if validate_upload_path_absolute and not is_absolute_upload_storage_path(
            values.get("upload_storage_path")
        ):
            errors.append("UPLOAD_STORAGE_PATH must be absolute in staging/production")

    upload_paths = _path_variants(values.get("upload_storage_path"))
    source_root = values.get("source_root")
    if source_root and any(
        _is_within(upload_path, source_path)
        for upload_path, source_path in zip(upload_paths, _path_variants(source_root), strict=True)
    ):
        errors.append("UPLOAD_STORAGE_PATH must be outside the backend source tree")
    if any(
        part.lower() in _WEB_DIRECTORY_NAMES for upload_path in upload_paths for part in upload_path.parts
    ):
        errors.append("UPLOAD_STORAGE_PATH must not be inside a web-served directory")

    return errors
