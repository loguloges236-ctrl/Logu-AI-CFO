import base64
import os
import unittest
from unittest.mock import patch

from app.core.config import Settings, default_upload_storage_path
from app.core.settings_validation import (
    is_absolute_upload_storage_path,
    is_valid_fernet_key,
    validate_settings,
)

GOOD_FERNET = base64.urlsafe_b64encode(os.urandom(32)).decode()
GOOD_HMAC = "h" * 40
GOOD_BOOTSTRAP = "b" * 40
# An absolute upload path for the host OS. A POSIX literal is not absolute on Windows.
VALID_UPLOADS = r"C:\ProgramData\Logu AI CFO\uploads" if os.name == "nt" else "/var/lib/logu-ai-cfo/uploads"


def base(**overrides):
    values = {
        "environment": "production",
        "database_url": "postgresql+psycopg://logu:x@db:5432/logu_cfo",
        "frontend_origins": ["https://cfo.example.com"],
        "trusted_hosts": ["cfo.example.com"],
        "trusted_proxy_networks": ["172.28.10.10/32"],
        "force_https": True,
        "cookie_secure": True,
        "enable_bootstrap": False,
        "bootstrap_token": "",
        "security_hmac_key": GOOD_HMAC,
        "data_encryption_key": GOOD_FERNET,
        "upload_storage_path": VALID_UPLOADS,
        "ai_provider": "disabled",
        "notification_provider": "disabled",
        "source_root": "/srv/app/backend",
    }
    values.update(overrides)
    return values


class SettingsValidationTests(unittest.TestCase):
    def test_valid_production_configuration_has_no_errors(self):
        self.assertEqual(validate_settings(base()), [])

    def test_production_rejects_env_example_placeholder_hmac_key(self):
        placeholder = "CHANGE-ME-replace-with-output-of-openssl-rand-hex-32"
        errors = validate_settings(base(security_hmac_key=placeholder))
        self.assertTrue(any("placeholder" in e for e in errors))

    def test_development_accepts_env_example_placeholder_hmac_key(self):
        placeholder = "CHANGE-ME-replace-with-output-of-openssl-rand-hex-32"
        errors = validate_settings(base(environment="development", security_hmac_key=placeholder))
        self.assertEqual(errors, [])

    def test_internal_api_hosts_must_be_single_label_names(self):
        for bad in (["backend.internal"], ["*"], ["10.0.0.5"], ["backend:8000"], ["localhost"]):
            errors = validate_settings(base(internal_api_hosts=bad))
            self.assertTrue(any("INTERNAL_API_HOSTS" in e for e in errors), bad)

    def test_internal_api_host_backend_is_valid(self):
        self.assertEqual(validate_settings(base(internal_api_hosts=["backend"])), [])

    def test_production_rejects_bootstrap(self):
        errors = validate_settings(base(enable_bootstrap=True, bootstrap_token=GOOD_BOOTSTRAP))
        self.assertTrue(any("ENABLE_BOOTSTRAP" in e for e in errors))

    def test_bootstrap_is_disabled_by_default_when_environment_does_not_set_it(self):
        values = base()
        values.pop("enable_bootstrap")
        values.pop("source_root")
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None, **values)
        self.assertFalse(settings.enable_bootstrap)

    def test_explicit_bootstrap_false_overrides_environment(self):
        values = base()
        values.pop("source_root")
        with patch.dict(os.environ, {"ENABLE_BOOTSTRAP": "true"}, clear=False):
            settings = Settings(_env_file=None, **values)
        self.assertFalse(settings.enable_bootstrap)

    def test_bootstrap_token_must_be_long_when_enabled(self):
        errors = validate_settings(
            base(environment="development", enable_bootstrap=True, bootstrap_token="short")
        )
        self.assertTrue(any("BOOTSTRAP_TOKEN" in e for e in errors))

    def test_production_requires_https_and_secure_cookies(self):
        errors = validate_settings(base(force_https=False, cookie_secure=False))
        self.assertTrue(any("FORCE_HTTPS" in e for e in errors))
        self.assertTrue(any("COOKIE_SECURE" in e for e in errors))

    def test_production_rejects_local_and_wildcard_hosts(self):
        for hosts in (["localhost"], ["127.0.0.1"], ["*"]):
            errors = validate_settings(base(trusted_hosts=hosts))
            self.assertTrue(any("TRUSTED_HOSTS" in e for e in errors), hosts)

    def test_production_requires_exact_https_origins(self):
        errors = validate_settings(base(frontend_origins=["http://cfo.example.com"]))
        self.assertTrue(any("FRONTEND_ORIGINS" in e for e in errors))

    def test_production_requires_keys(self):
        errors = validate_settings(base(security_hmac_key="", data_encryption_key=""))
        self.assertTrue(any("SECURITY_HMAC_KEY" in e for e in errors))
        self.assertTrue(any("DATA_ENCRYPTION_KEY" in e for e in errors))

    def test_malformed_fernet_key_is_rejected_without_echoing_it(self):
        bad = "not-a-fernet-key-value-that-is-clearly-wrong"
        errors = validate_settings(base(data_encryption_key=bad))
        self.assertTrue(any("DATA_ENCRYPTION_KEY" in e for e in errors))
        self.assertFalse(any(bad in e for e in errors))

    def test_fernet_key_length_check(self):
        self.assertTrue(is_valid_fernet_key(GOOD_FERNET))
        self.assertFalse(is_valid_fernet_key(base64.urlsafe_b64encode(b"x" * 16).decode()))

    def test_database_must_be_postgresql(self):
        errors = validate_settings(base(database_url="sqlite:///./logu.db"))
        self.assertTrue(any("PostgreSQL" in e for e in errors))

    def test_providers_must_stay_disabled(self):
        errors = validate_settings(base(ai_provider="openai"))
        self.assertTrue(any("AI_PROVIDER" in e for e in errors))

    def test_invalid_proxy_network_is_rejected(self):
        errors = validate_settings(base(trusted_proxy_networks=["not-a-network"]))
        self.assertTrue(any("TRUSTED_PROXY_NETWORKS" in e for e in errors))

    def test_upload_path_inside_source_tree_is_rejected(self):
        errors = validate_settings(base(upload_storage_path="/srv/app/backend/runtime/uploads"))
        self.assertTrue(any("source tree" in e for e in errors))

    def test_upload_path_in_web_directory_is_rejected(self):
        errors = validate_settings(base(upload_storage_path="/var/www/public/uploads"))
        self.assertTrue(any("web-served" in e for e in errors))

    def test_production_accepts_posix_and_windows_absolute_upload_paths(self):
        for path in ("/var/lib/logu-ai-cfo/uploads", r"F:\Logu AI CFO V1\Logu-AI-CFO\uploads"):
            with self.subTest(path=path):
                self.assertTrue(is_absolute_upload_storage_path(path))
                self.assertEqual(validate_settings(base(upload_storage_path=path)), [])

    def test_production_upload_path_must_not_be_relative_in_either_syntax(self):
        for path in ("relative/uploads", r"C:relative\uploads", r"\relative\uploads"):
            with self.subTest(path=path):
                self.assertFalse(is_absolute_upload_storage_path(path))
                errors = validate_settings(base(upload_storage_path=path))
                self.assertTrue(any("absolute" in error for error in errors))

    def test_windows_default_upload_path_uses_the_project_f_drive(self):
        with patch("app.core.config.os.name", "nt"):
            path = default_upload_storage_path()
        self.assertEqual(str(path), r"F:\Logu AI CFO V1\Logu-AI-CFO\Logu-AI-CFO\uploads")


if __name__ == "__main__":
    unittest.main()
