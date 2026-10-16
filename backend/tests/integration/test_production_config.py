"""Startup configuration guards. Constructing Settings must fail for insecure production values."""


import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings

GOOD = {
    "environment": "production",
    "database_url": "postgresql+psycopg://logu:x@db:5432/logu_cfo",
    "frontend_origins": ["https://cfo.example.com"],
    "trusted_hosts": ["cfo.example.com"],
    "trusted_proxy_networks": ["172.28.10.10/32"],
    "force_https": True,
    "cookie_secure": True,
    # Explicitly override the test harness's bootstrap environment variable.
    # Settings init values take precedence over environment values.
    "enable_bootstrap": False,
    "security_hmac_key": "k" * 40,
    "data_encryption_key": Fernet.generate_key().decode(),
    "upload_storage_path": "/var/lib/logu-ai-cfo/uploads",
    "clamav_host": "clamav",
}


def test_secure_production_settings_are_accepted():
    Settings(_env_file=None, **GOOD)


def test_direct_settings_values_override_bootstrap_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_BOOTSTRAP", "true")

    settings = Settings(_env_file=None, **GOOD)

    assert settings.enable_bootstrap is False


@pytest.mark.parametrize(
    "override",
    [
        {"enable_bootstrap": True, "bootstrap_token": "t" * 40},
        {"force_https": False},
        {"cookie_secure": False},
        {"trusted_hosts": ["localhost"]},
        {"frontend_origins": ["http://cfo.example.com"]},
        {"security_hmac_key": ""},
        {"data_encryption_key": ""},
        {"data_encryption_key": "not-a-fernet-key"},
        {"database_url": "sqlite:///./logu.db"},
        {"ai_provider": "openai"},
        {"upload_storage_path": "relative/path"},
    ],
)
def test_insecure_production_settings_fail_startup(override):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{**GOOD, **override})


def test_settings_error_messages_do_not_contain_secret_values():
    secret = "test-hmac-key-too-short"
    with pytest.raises(ValueError) as exc_info:
        Settings(_env_file=None, **{**GOOD, "security_hmac_key": secret})
    assert secret not in str(exc_info.value)
