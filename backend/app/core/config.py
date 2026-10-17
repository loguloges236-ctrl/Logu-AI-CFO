from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_validation import is_absolute_upload_storage_path, validate_settings


def default_upload_storage_path() -> Path:
    """Return the host-appropriate absolute default for the upload store.

    Production-like environments require an absolute path. The Windows location
    deliberately remains on the project's existing F: drive, outside the backend
    source tree and any web-served directory.
    """
    if os.name == "nt":
        return Path(r"F:\Logu AI CFO V1\Logu-AI-CFO\Logu-AI-CFO\uploads")
    return Path("/var/lib/logu-ai-cfo/uploads")

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """All runtime configuration. Secrets come from the environment or a secret manager only."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, enable_decoding=False
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    database_url: str = ""
    frontend_origins: list[str] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    # Single-label names of internal services that call the API directly on the private network
    # (compose: "backend"). Not reachable through the public edge; see EdgeMiddleware.
    internal_api_hosts: list[str] = Field(default_factory=list)
    force_https: bool = False
    # Phase 3 business rules. Defaults refuse over-receipt and over-invoicing; partial invoicing is allowed.
    commerce_allow_over_receipt: bool = False
    commerce_allow_over_invoice: bool = False
    commerce_allow_partial_invoice: bool = True
    # Only requests whose immediate peer is inside these networks may supply
    # X-Forwarded-For / X-Forwarded-Proto. Everything else is ignored.
    trusted_proxy_networks: list[str] = Field(default_factory=list)

    session_cookie_name: str = "logu_session"
    csrf_cookie_name: str = "logu_csrf"
    device_cookie_name: str = "logu_device"
    session_ttl_minutes: int = Field(default=480, ge=15, le=43_200)
    session_idle_minutes: int = Field(default=60, ge=5, le=10_080)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict"] = "lax"

    enable_bootstrap: bool = False
    bootstrap_token: str = ""

    throttle_window_minutes: int = Field(default=15, ge=1, le=1440)
    login_ip_email_limit: int = Field(default=5, ge=1, le=100)
    login_email_limit: int = Field(default=30, ge=1, le=1000)
    login_ip_limit: int = Field(default=100, ge=1, le=10_000)
    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=240)

    # Keys for keyed hashing of tokens and IP addresses (HMAC-SHA256). Required in production.
    security_hmac_key: str = ""
    # Fernet key for encrypting uploaded document bytes and sensitive fields. Required in production.
    data_encryption_key: str = ""
    # Comma-separated retired Fernet keys, accepted for decryption only during a rotation (docs/KEY_ROTATION.md).
    data_encryption_key_previous: str = ""

    upload_storage_path: Path = Field(default_factory=default_upload_storage_path)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    clamav_host: str = "clamav"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    stale_scan_minutes: int = Field(default=15, ge=1, le=1440)

    # No AI or notification provider is implemented in Phase 1; values other than "disabled" fail startup.
    ai_provider: str = "disabled"
    notification_provider: str = "disabled"

    @field_validator("frontend_origins", "trusted_hosts", "internal_api_hosts", "trusted_proxy_networks", mode="before")
    @classmethod
    def _split_lists(cls, value: object) -> object:
        return _split_csv(value)

    @property
    def expose_api_docs(self) -> bool:
        return self.environment == "development"

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_upload_storage_path(cls, values: object) -> object:
        """Validate a supplied deployment path before host-specific ``Path`` coercion.

        On Windows, ``Path("/var/lib/...")`` becomes ``\\var\\lib\\...`` and
        can no longer be identified as the POSIX absolute path originally supplied.
        """
        if not isinstance(values, dict):
            return values
        if (
            str(values.get("environment", "development")) in {"staging", "production"}
            and values.get("upload_storage_path") is not None
            and not is_absolute_upload_storage_path(values["upload_storage_path"])
        ):
            raise ValueError("UPLOAD_STORAGE_PATH must be absolute in staging/production")
        return values

    @model_validator(mode="after")
    def _validate_configuration(self) -> Settings:
        errors = validate_settings(
            {
                "environment": self.environment,
                "database_url": self.database_url,
                "frontend_origins": self.frontend_origins,
                "trusted_hosts": self.trusted_hosts,
                "internal_api_hosts": self.internal_api_hosts,
                "trusted_proxy_networks": self.trusted_proxy_networks,
                "force_https": self.force_https,
                "cookie_secure": self.cookie_secure,
                "enable_bootstrap": self.enable_bootstrap,
                "bootstrap_token": self.bootstrap_token,
                "security_hmac_key": self.security_hmac_key,
                "data_encryption_key": self.data_encryption_key,
                "data_encryption_key_previous": self.data_encryption_key_previous,
                "upload_storage_path": str(self.upload_storage_path),
                "ai_provider": self.ai_provider,
                "notification_provider": self.notification_provider,
                "source_root": str(BACKEND_ROOT),
            },
            # The raw source path was checked before Pydantic converted it to the host Path type.
            validate_upload_path_absolute=False,
        )
        if errors:
            raise ValueError("Invalid configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
