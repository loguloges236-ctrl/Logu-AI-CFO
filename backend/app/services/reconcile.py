"""Storage reconciliation: finds and (optionally) removes orphaned blobs and stuck scans.

Run from the CLI (`python -m app.cli reconcile-storage`). Dry-run is the default.

A stored blob is legitimate only when its file row is CLEAN, not yet storage-deleted, and its document
is ACTIVE. Anything else is an orphan. A grace period protects blobs that are still being written by an
in-flight upload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from app.core.clock import utcnow
from app.core.config import get_settings
from app.models import Document, DocumentFile, DocumentStatus, FileScanStatus
from app.storage.local import LocalFileStorage

ORPHAN_GRACE_PERIOD = timedelta(hours=1)


@dataclass
class ReconcileReport:
    applied: bool
    orphaned_blobs: list[str] = field(default_factory=list)
    missing_clean_blobs: list[str] = field(default_factory=list)
    stuck_scans: list[str] = field(default_factory=list)


def reconcile_storage(db: DBSession, storage: LocalFileStorage, *, apply: bool) -> ReconcileReport:
    now = utcnow()
    rows = db.execute(
        select(
            DocumentFile.storage_key,
            DocumentFile.scan_status,
            DocumentFile.storage_deleted_at,
            Document.status,
        ).join(Document, Document.id == DocumentFile.document_id)
    ).all()
    legitimate = {
        key
        for key, scan_status, deleted_at, document_status in rows
        if scan_status == FileScanStatus.CLEAN.value
        and deleted_at is None
        and document_status == DocumentStatus.ACTIVE.value
    }
    on_disk = set(storage.iter_keys())

    report = ReconcileReport(applied=apply)
    for key in sorted(on_disk - legitimate):
        modified = datetime.fromtimestamp(storage.modified_at(key), UTC)
        if now - modified < ORPHAN_GRACE_PERIOD:
            continue
        report.orphaned_blobs.append(key)
        if apply:
            storage.delete(key)

    report.missing_clean_blobs = sorted(legitimate - on_disk)

    stale_before = now - timedelta(minutes=get_settings().stale_scan_minutes)
    stuck = list(
        db.scalars(
            select(DocumentFile).where(
                DocumentFile.scan_status.in_(
                    [FileScanStatus.PENDING.value, FileScanStatus.SCANNING.value]
                ),
                DocumentFile.created_at < stale_before,
            )
        )
    )
    report.stuck_scans = [file.storage_key for file in stuck]
    if apply and stuck:
        for file in stuck:
            file.scan_status = FileScanStatus.FAILED.value
            db.execute(
                update(Document)
                .where(Document.id == file.document_id)
                .values(status=DocumentStatus.QUARANTINED.value)
            )
        db.commit()
    return report
