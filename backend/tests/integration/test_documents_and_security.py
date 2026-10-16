"""Document pipeline (scanning states, encryption at rest, download, deletion, reconciliation) and
HTTP security-policy tests. Requires TEST_DATABASE_URL (PostgreSQL)."""

import hashlib
import os
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.main import app
from app.models import AuditLog, Document, DocumentFile
from app.security.crypto import SensitiveDataCipher
from app.services.reconcile import ORPHAN_GRACE_PERIOD, reconcile_storage
from tests.conftest import csrf_headers, sample_pdf


def _upload(client, content=None, content_type="application/pdf", name="bill.pdf", kind="bill"):
    return client.post(
        "/api/v1/documents",
        headers=csrf_headers(client),
        data={"kind": kind},
        files={"file": (name, content if content is not None else sample_pdf(), content_type)},
    )


def _db():
    return get_session_factory()()


# ---- intake validation -------------------------------------------------------------------------

def test_upload_requires_authentication_and_csrf(owner_client, client):
    unauthenticated = TestClient(app, base_url="http://testserver")
    assert unauthenticated.post("/api/v1/documents", data={"kind": "bill"},
                                files={"file": ("a.pdf", sample_pdf(), "application/pdf")}).status_code == 401
    missing_csrf = owner_client.post("/api/v1/documents", data={"kind": "bill"},
                                     files={"file": ("a.pdf", sample_pdf(), "application/pdf")})
    assert missing_csrf.status_code == 403


def test_declared_type_must_match_magic_bytes(owner_client, harness):
    response = _upload(owner_client, content=b"<html>not a pdf</html>")
    assert response.status_code == 415
    assert harness["scanner"].calls == []  # nothing is scanned or stored when intake validation fails


def test_unsupported_declared_type_is_refused(owner_client):
    assert _upload(owner_client, content_type="text/html", name="a.html").status_code == 415


def test_oversized_upload_is_refused(owner_client):
    limit = get_settings().max_upload_bytes
    response = _upload(owner_client, content=b"%PDF-" + b"0" * (limit + 10))
    assert response.status_code == 413


def test_empty_upload_is_refused(owner_client):
    assert _upload(owner_client, content=b"").status_code == 422


# ---- scan states ---------------------------------------------------------------------------------

def test_clean_upload_is_active_encrypted_and_downloadable(owner_client, harness):
    content = sample_pdf()
    response = _upload(owner_client, content=content)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active"
    file_info = body["files"][0]
    assert file_info["scan_status"] == "clean"
    assert file_info["downloadable"] is True
    assert file_info["sha256"] == hashlib.sha256(content).hexdigest()

    with _db() as db:
        row = db.scalar(select(DocumentFile).where(DocumentFile.id.is_not(None)))
        assert row.encryption_scheme == "fernet-v1"
    blob = harness["storage"].read(row.storage_key)
    assert content not in blob  # plaintext never reaches disk
    assert SensitiveDataCipher(get_settings().data_encryption_key).decrypt_bytes(blob) == content


def test_infected_upload_is_quarantined_with_no_bytes_stored(owner_client, harness):
    harness["scanner"].verdict = "INFECTED"
    harness["scanner"].signature = "Test.Signature"
    response = _upload(owner_client)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "malware_detected"
    with _db() as db:
        document = db.scalar(select(Document))
        file = db.scalar(select(DocumentFile))
    assert document.status == "quarantined"
    assert file.scan_status == "infected"
    assert file.scan_signature == "Test.Signature"
    assert list(harness["storage_dir"].rglob("*.tmp")) == []
    assert not harness["storage"].exists(file.storage_key)


def test_scanner_outage_fails_closed_and_is_never_marked_clean(owner_client, harness):
    harness["scanner"].fail = True
    response = _upload(owner_client)
    assert response.status_code == 503
    with _db() as db:
        file = db.scalar(select(DocumentFile))
        document = db.scalar(select(Document))
    assert file.scan_status == "failed"
    assert document.status == "quarantined"
    assert not harness["storage"].exists(file.storage_key)


def test_quarantined_document_cannot_be_downloaded(owner_client, harness):
    harness["scanner"].verdict = "INFECTED"
    _upload(owner_client)
    listed = owner_client.get("/api/v1/documents").json()["documents"][0]
    file_id = listed["files"][0]["id"]
    response = owner_client.get(f"/api/v1/documents/{listed['id']}/files/{file_id}")
    assert response.status_code == 409


# ---- download, audit, deletion -------------------------------------------------------------------

def test_download_returns_original_bytes_as_attachment_and_is_audited(owner_client):
    content = sample_pdf()
    created = _upload(owner_client, content=content, name="salary slip.pdf").json()
    file_id = created["files"][0]["id"]
    response = owner_client.get(f"/api/v1/documents/{created['id']}/files/{file_id}")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    with _db() as db:
        actions = set(db.scalars(select(AuditLog.action)))
    assert {"document_upload_received", "document_scan_completed", "document_stored", "document_downloaded"} <= actions


def test_download_detects_storage_tampering(owner_client, harness):
    created = _upload(owner_client).json()
    with _db() as db:
        file = db.scalar(select(DocumentFile))
    cipher = SensitiveDataCipher(get_settings().data_encryption_key)
    harness["storage"].delete(file.storage_key)
    harness["storage"].put(file.storage_key, cipher.encrypt_bytes(b"%PDF-1.4 replaced content"))
    response = owner_client.get(f"/api/v1/documents/{created['id']}/files/{file.id}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "document_integrity_error"


def test_deleting_a_document_removes_bytes_and_hides_it(owner_client, harness):
    created = _upload(owner_client).json()
    with _db() as db:
        file = db.scalar(select(DocumentFile))
    assert harness["storage"].exists(file.storage_key)
    response = owner_client.delete(f"/api/v1/documents/{created['id']}", headers=csrf_headers(owner_client))
    assert response.status_code == 200
    assert not harness["storage"].exists(file.storage_key)
    assert owner_client.get("/api/v1/documents").json()["documents"] == []
    assert owner_client.get(f"/api/v1/documents/{created['id']}").status_code == 404
    assert owner_client.get(f"/api/v1/documents/{created['id']}/files/{file.id}").status_code == 404


def test_unknown_document_ids_return_404(owner_client):
    import uuid

    missing = uuid.uuid4()
    assert owner_client.get(f"/api/v1/documents/{missing}").status_code == 404
    assert owner_client.delete(f"/api/v1/documents/{missing}", headers=csrf_headers(owner_client)).status_code == 404


def test_filename_path_components_are_stripped(owner_client):
    created = _upload(owner_client, name="../../etc/passwd.pdf").json()
    assert created["display_name"] == "passwd.pdf"


# ---- reconciliation -------------------------------------------------------------------------------

def test_reconcile_dry_run_then_apply_removes_only_old_orphans(owner_client, harness):
    import uuid

    from app.security.keys import new_storage_key

    orphan = new_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    harness["storage"].put(orphan, b"ciphertext-without-a-row")
    old = time.time() - ORPHAN_GRACE_PERIOD.total_seconds() - 60
    os.utime(harness["storage"].root / orphan, (old, old))

    with _db() as db:
        dry = reconcile_storage(db, harness["storage"], apply=False)
    assert orphan in dry.orphaned_blobs
    assert harness["storage"].exists(orphan)

    with _db() as db:
        applied = reconcile_storage(db, harness["storage"], apply=True)
    assert orphan in applied.orphaned_blobs
    assert not harness["storage"].exists(orphan)


def test_reconcile_keeps_fresh_blobs_within_grace_period(harness):
    import uuid

    from app.security.keys import new_storage_key

    fresh = new_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    harness["storage"].put(fresh, b"being-written")
    with _db() as db:
        report = reconcile_storage(db, harness["storage"], apply=True)
    assert fresh not in report.orphaned_blobs
    assert harness["storage"].exists(fresh)


# ---- HTTP security policy --------------------------------------------------------------------------

def test_api_responses_carry_security_headers(owner_client):
    response = owner_client.get("/api/v1/auth/me")
    headers = response.headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["x-request-id"]


def test_unknown_host_header_is_rejected(harness):
    bad = TestClient(app, base_url="http://evil.example")
    response = bad.get("/api/v1/auth/me")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_host"


def test_health_endpoints_are_reachable_without_host_allowlisting(harness):
    bad = TestClient(app, base_url="http://169.254.169.254")
    assert bad.get("/health").status_code == 200
    ready = bad.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_openapi_is_disabled_outside_development(harness):
    # Test environment is not "development": the schema must not be served.
    client = TestClient(app, base_url="http://testserver")
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_validation_errors_do_not_echo_submitted_values(owner_client):
    secret_value = "SUBMITTED-SECRET-VALUE-123"
    response = owner_client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(owner_client),
        json={"current_password": secret_value, "new_password": 12},
    )
    assert response.status_code == 422
    errors = response.json()["error"]["details"]["errors"]
    assert errors and all(set(item) == {"loc", "type"} for item in errors)
    assert secret_value not in response.text
