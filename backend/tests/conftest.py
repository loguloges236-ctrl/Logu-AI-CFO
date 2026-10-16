"""Shared fixtures for PostgreSQL-backed integration tests.

Environment must be configured BEFORE `app` is imported, because settings are read at import time.
TEST_DATABASE_URL must point to a disposable PostgreSQL database: every test drops and recreates `public`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if not TEST_DATABASE_URL:
    raise pytest.UsageError(
        "TEST_DATABASE_URL is required, e.g. postgresql+psycopg://logu:pw@localhost:5432/logu_test. "
        "The database is dropped and recreated by the test suite."
    )

from cryptography.fernet import Fernet  # noqa: E402

_UPLOADS = tempfile.mkdtemp(prefix="logu-test-uploads-")  # outside the source tree
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "ENABLE_BOOTSTRAP": "true",
        "BOOTSTRAP_TOKEN": "test-only-bootstrap-token-0123456789abcdef",
        "SECURITY_HMAC_KEY": "test-only-hmac-key-0123456789abcdef0123456789",
        "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "UPLOAD_STORAGE_PATH": _UPLOADS,
        "TRUSTED_HOSTS": "testserver",
        "FRONTEND_ORIGINS": "http://testserver",
        "TRUSTED_PROXY_NETWORKS": "172.28.10.10/32",
        "CLAMAV_HOST": "127.0.0.1",
        "CLAMAV_PORT": "1",
    }
)

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.api.deps import get_scanner, get_storage  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.storage.clamav import ScannerError, ScanResult  # noqa: E402
from app.storage.local import LocalFileStorage  # noqa: E402

PASSWORD = "this-is-a-long-test-password"
EMAIL = "owner@example.com"
BOOTSTRAP_HEADER = {"X-Setup-Token": os.environ["BOOTSTRAP_TOKEN"]}


@dataclass
class FakeScanner:
    """Test double. Not shipped in the application package."""

    verdict: str = "CLEAN"
    signature: str | None = None
    fail: bool = False
    calls: list[int] = field(default_factory=list)

    def scan(self, data: bytes) -> ScanResult:
        self.calls.append(len(data))
        if self.fail:
            raise ScannerError("simulated scanner outage")
        return ScanResult(self.verdict, self.signature)


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


@pytest.fixture()
def fresh_db():
    with get_engine().begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(_alembic_config(), "head")
    yield


@pytest.fixture()
def harness(fresh_db):
    storage_dir = Path(tempfile.mkdtemp(prefix="logu-blobs-"))
    storage = LocalFileStorage(storage_dir)
    scanner = FakeScanner()
    app.dependency_overrides[get_scanner] = lambda: scanner
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield {"scanner": scanner, "storage": storage, "storage_dir": storage_dir}
    finally:
        app.dependency_overrides.clear()
        shutil.rmtree(storage_dir, ignore_errors=True)


@pytest.fixture()
def client(harness):
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture()
def owner_client(client):
    """A client signed in as the sole owner."""
    created = client.post(
        "/api/v1/auth/bootstrap",
        json={"email": EMAIL, "password": PASSWORD},
        headers=BOOTSTRAP_HEADER,
    )
    assert created.status_code == 201, created.text
    login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return client


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("logu_csrf", "")}


def sample_pdf() -> bytes:
    return b"%PDF-1.7\n" + os.urandom(64) + b"\n%%EOF\n"
