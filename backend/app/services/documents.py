"""Document intake, scanning, encrypted storage, download and revocation.

Upload state machine (persisted at each step, so a crash leaves an inspectable state):

    document: PENDING --(CLEAN, stored)--> ACTIVE
              PENDING --(INFECTED | FAILED)--> QUARANTINED   (no bytes stored)
              any     --(owner deletes)-----> REVOKED        (bytes removed, metadata kept)

    file:     PENDING -> SCANNING -> CLEAN | INFECTED | FAILED

Only ACTIVE documents whose file is CLEAN can be downloaded. Bytes are scanned in memory as plaintext,
then stored as Fernet ciphertext. The database holds the plaintext SHA-256 for integrity checks.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

from fastapi import Request, UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import APIError
from app.models import Document, DocumentFile, DocumentKind, DocumentStatus, FileScanStatus, Owner
from app.security.crypto import CryptoError, SensitiveDataCipher
from app.security.keys import new_storage_key
from app.services.audit import write_audit
from app.storage.clamav import ScannerError
from app.storage.file_rules import (
    ALLOWED_CONTENT_TYPES,
    detect_content_type,
    safe_display_name,
)
from app.storage.local import LocalFileStorage, StorageError, StorageNotFound

logger = logging.getLogger(__name__)

ENCRYPTION_SCHEME = "fernet-v1"

def _finish_scan(
    db: DBSession,
    request: Request,
    *,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    document: Document,
    file: DocumentFile,
    scan_status: FileScanStatus,
    signature: str | None,
) -> None:
    file.scan_status = scan_status.value
    file.scan_signature = signature
    file.scanned_at = utcnow()
    if scan_status in (FileScanStatus.INFECTED, FileScanStatus.FAILED):
        document.status = DocumentStatus.QUARANTINED.value
    write_audit(
        db,
        request,
        action="document_scan_completed",
        success=scan_status == FileScanStatus.CLEAN,
        owner_id=owner_id,
        session_id=session_id,
        target_type="document",
        target_id=str(document.id),
        metadata={"result": scan_status.value},
    )
    db.commit()


def store_document(
    *,
    db: DBSession,
    request: Request,
    owner: Owner,
    session_id: uuid.UUID,
    kind: DocumentKind,
    upload: UploadFile,
    scanner,
    storage: LocalFileStorage,
    cipher: SensitiveDataCipher,
) -> Document:
    """Synchronous on purpose: the scanner and storage perform blocking I/O."""
    settings = get_settings()
    declared = (upload.content_type or "").lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        raise APIError(
            415, "unsupported_file_type", "Only PDF, JPG, PNG and WEBP documents are allowed."
        )
    content = upload.file.read(settings.max_upload_bytes + 1)
    if not content:
        raise APIError(422, "empty_upload", "The document is empty.")
    if len(content) > settings.max_upload_bytes:
        raise APIError(413, "file_too_large", "The document exceeds the maximum upload size.")
    detected = detect_content_type(content)
    if detected is None or detected != declared:
        raise APIError(
            415, "invalid_file_content", "The file content does not match its declared type."
        )

    display_name = safe_display_name(upload.filename)
    document_id = uuid.uuid4()
    file_id = uuid.uuid4()
    document = Document(
        id=document_id,
        owner_id=owner.id,
        kind=kind.value,
        status=DocumentStatus.PENDING.value,
        display_name=display_name,
    )
    file = DocumentFile(
        id=file_id,
        document_id=document_id,
        storage_key=new_storage_key(owner.id, document_id, file_id),
        original_filename=display_name,
        declared_content_type=declared,
        content_type=detected,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        scan_status=FileScanStatus.PENDING.value,
    )
    db.add(document)
    db.add(file)
    write_audit(
        db,
        request,
        action="document_upload_received",
        success=True,
        owner_id=owner.id,
        session_id=session_id,
        target_type="document",
        target_id=str(document_id),
        metadata={"kind": kind.value, "content_type": detected, "size_bytes": len(content)},
    )
    db.commit()

    file.scan_status = FileScanStatus.SCANNING.value
    db.commit()

    try:
        verdict = scanner.scan(content)
    except ScannerError:
        logger.warning("Malware scan could not be completed for document %s.", document_id)
        _finish_scan(
            db, request, owner_id=owner.id, session_id=session_id, document=document, file=file,
            scan_status=FileScanStatus.FAILED, signature=None,
        )
        raise APIError(
            503, "malware_scan_unavailable", "The document could not be scanned. Try again later."
        ) from None

    if verdict.status != "CLEAN":
        _finish_scan(
            db, request, owner_id=owner.id, session_id=session_id, document=document, file=file,
            scan_status=FileScanStatus.INFECTED, signature=verdict.signature,
        )
        raise APIError(422, "malware_detected", "The document failed the security scan.")

    storage_key = file.storage_key
    try:
        ciphertext = cipher.encrypt_bytes(content)
        storage.put(storage_key, ciphertext)
    except (StorageError, CryptoError):
        logger.error("Encrypted document could not be stored for document %s.", document_id)
        _finish_scan(
            db, request, owner_id=owner.id, session_id=session_id, document=document, file=file,
            scan_status=FileScanStatus.FAILED, signature=None,
        )
        raise APIError(
            503, "document_storage_unavailable", "The document could not be stored. Try again later."
        ) from None

    try:
        file.scan_status = FileScanStatus.CLEAN.value
        file.scan_signature = None
        file.scanned_at = utcnow()
        file.encryption_scheme = ENCRYPTION_SCHEME
        document.status = DocumentStatus.ACTIVE.value
        write_audit(
            db, request, action="document_scan_completed", success=True, owner_id=owner.id,
            session_id=session_id, target_type="document", target_id=str(document_id),
            metadata={"result": FileScanStatus.CLEAN.value},
        )
        write_audit(
            db, request, action="document_stored", success=True, owner_id=owner.id,
            session_id=session_id, target_type="document", target_id=str(document_id),
        )
        db.commit()
    except Exception:
        # Orphan prevention: the bytes are removed when the metadata cannot be committed.
        db.rollback()
        _remove_blob_quietly(storage, storage_key)
        _mark_failed_quietly(db, document_id, file_id)
        raise
    return document


def _remove_blob_quietly(storage: LocalFileStorage, key: str) -> None:
    try:
        storage.delete(key)
    except StorageError:
        logger.error("Orphaned encrypted blob could not be removed; reconciliation will retry.")


def _mark_failed_quietly(db: DBSession, document_id: uuid.UUID, file_id: uuid.UUID) -> None:
    try:
        db.execute(
            update(DocumentFile)
            .where(DocumentFile.id == file_id)
            .values(scan_status=FileScanStatus.FAILED.value)
        )
        db.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=DocumentStatus.QUARANTINED.value)
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.error("Could not record failed document state; reconciliation will correct it.")


def list_documents(db: DBSession, owner_id: uuid.UUID) -> list[tuple[Document, list[DocumentFile]]]:
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.owner_id == owner_id, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
    )
    files_by_document: dict[uuid.UUID, list[DocumentFile]] = {document.id: [] for document in documents}
    if documents:
        for file in db.scalars(
            select(DocumentFile).where(DocumentFile.document_id.in_(list(files_by_document)))
        ):
            files_by_document[file.document_id].append(file)
    return [(document, files_by_document[document.id]) for document in documents]


def get_owner_document(db: DBSession, owner_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.owner_id == owner_id, Document.deleted_at.is_(None)
        )
    )
    if document is None:
        raise APIError(404, "document_not_found", "Document not found.")
    return document


def get_document_file(db: DBSession, document: Document, file_id: uuid.UUID) -> DocumentFile:
    file = db.scalar(
        select(DocumentFile).where(DocumentFile.id == file_id, DocumentFile.document_id == document.id)
    )
    if file is None:
        raise APIError(404, "document_file_not_found", "Document file not found.")
    return file


def is_downloadable(document: Document, file: DocumentFile) -> bool:
    return (
        document.status == DocumentStatus.ACTIVE.value
        and file.scan_status == FileScanStatus.CLEAN.value
        and file.storage_deleted_at is None
    )


def read_for_download(
    *,
    db: DBSession,
    request: Request,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    document: Document,
    file: DocumentFile,
    storage: LocalFileStorage,
    cipher: SensitiveDataCipher,
) -> bytes:
    if not is_downloadable(document, file):
        raise APIError(409, "document_not_available", "This document is not available for download.")
    try:
        plaintext = cipher.decrypt_bytes(storage.read(file.storage_key))
    except StorageNotFound:
        raise APIError(404, "document_file_missing", "The stored file is unavailable.") from None
    except StorageError:
        raise APIError(503, "document_storage_unavailable", "The document is temporarily unavailable.") from None
    except CryptoError:
        _audit_integrity_failure(db, request, owner_id, session_id, document, file)
        raise APIError(500, "document_integrity_error", "The document could not be verified.") from None

    if hashlib.sha256(plaintext).hexdigest() != file.sha256:
        _audit_integrity_failure(db, request, owner_id, session_id, document, file)
        raise APIError(500, "document_integrity_error", "The document could not be verified.")

    write_audit(
        db,
        request,
        action="document_downloaded",
        success=True,
        owner_id=owner_id,
        session_id=session_id,
        target_type="document",
        target_id=str(document.id),
        metadata={"file_id": str(file.id)},
    )
    db.commit()
    return plaintext


def _audit_integrity_failure(
    db: DBSession, request: Request, owner_id, session_id, document: Document, file: DocumentFile
) -> None:
    write_audit(
        db,
        request,
        action="document_integrity_failed",
        success=False,
        owner_id=owner_id,
        session_id=session_id,
        target_type="document",
        target_id=str(document.id),
        metadata={"file_id": str(file.id)},
    )
    db.commit()


def revoke_document(
    *, db: DBSession, request: Request, owner_id: uuid.UUID, session_id: uuid.UUID, document: Document
) -> list[str]:
    """Mark revoked and return storage keys to remove AFTER the commit succeeds."""
    now: datetime = utcnow()
    document.status = DocumentStatus.REVOKED.value
    document.deleted_at = now
    keys: list[str] = []
    files = db.scalars(select(DocumentFile).where(DocumentFile.document_id == document.id)).all()
    for file in files:
        if file.storage_deleted_at is None:
            file.storage_deleted_at = now
            keys.append(file.storage_key)
    write_audit(
        db,
        request,
        action="document_revoked",
        success=True,
        owner_id=owner_id,
        session_id=session_id,
        target_type="document",
        target_id=str(document.id),
    )
    db.commit()
    return keys


def remove_blobs_after_revoke(storage: LocalFileStorage, keys: list[str]) -> None:
    for key in keys:
        _remove_blob_quietly(storage, key)
