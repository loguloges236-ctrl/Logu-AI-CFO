"""Document-processing jobs: upload -> provider -> classification -> extraction -> validation -> review -> draft.

- The provider is injected (app.commerce.extraction.DocumentExtractionProvider). No OCR is bundled. With no provider
  configured the job says so and waits for manual entry. Nothing is ever invented.
- Provider values are stored as evidence and as a starting payload. They are never used to write business data.
- Approval creates a DRAFT (purchase bill, purchase order, quotation or sales order) through the same builders the
  owner uses by hand. It never posts stock or the ledger. Posting is a separate, explicit action.
- Corrections are re-validated from scratch and every corrected field is marked as owner-confirmed.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.commerce import extraction as ex
from app.core.clock import utcnow
from app.models import Document, DocumentFile, DocumentProcessingJob, DocumentStatus
from app.services import commerce_catalog as catalog
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, not_found, rule, unit_of_work
from app.services.commerce_purchasing import build_purchase_bill, build_purchase_order
from app.services.commerce_sales import _build_sales_order, build_quotation

CORRECTABLE_FIELDS = frozenset(
    {"document_type", "party_id", "party_name", "reference_number", "document_date", "due_date", "grand_total", "lines", "notes"}
)
ACKNOWLEDGEABLE_CODES = frozenset({"low_confidence", "ambiguous_classification"}) | ex.ACKNOWLEDGEABLE_DUPLICATE_CODES
MAX_LINES_IN_PAYLOAD = 200


def _json_safe(value: Any) -> Any:
    """Convert provider output into plain JSON. Decimals and dates become strings; binary floats are never kept."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value][:MAX_LINES_IN_PAYLOAD * 5]
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)[:500]


def get_job(db: DBSession, job_id: UUID) -> DocumentProcessingJob:
    job = db.get(DocumentProcessingJob, job_id)
    if job is None:
        raise not_found("Processing job")
    return job


def list_jobs(db: DBSession, status: str | None = None) -> list[DocumentProcessingJob]:
    stmt = select(DocumentProcessingJob).order_by(DocumentProcessingJob.created_at.desc())
    if status:
        stmt = stmt.where(DocumentProcessingJob.status == status)
    return list(db.scalars(stmt.limit(200)))


def create_job(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    document: Document,
    provider_name: str,
) -> DocumentProcessingJob:
    if document.status != DocumentStatus.ACTIVE.value or document.deleted_at is not None:
        raise rule("document_not_available", "Only an active, scanned document can be processed")
    if db.scalar(select(DocumentProcessingJob.id).where(DocumentProcessingJob.document_id == document.id)) is not None:
        raise rule("job_exists", "This document already has a processing job")
    with unit_of_work(db):
        job = DocumentProcessingJob(
            id=uuid.uuid4(),
            document_id=document.id,
            status="queued",
            provider_name=provider_name[:60],
            provider_available=False,
            raw_extraction={},
            current_payload={},
            field_confidence={},
            issues=[],
            created_by_owner_id=auth.owner.id,
        )
        db.add(job)
        audit(db, request, auth, "document_processing.queued", "document", str(document.id), {"job": str(job.id)[:8]})
    return job


def run_job(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    job_id: UUID,
    *,
    provider: ex.DocumentExtractionProvider,
    read_content: Callable[[], tuple[bytes, str]],
) -> DocumentProcessingJob:
    """Run the provider once. The job is saved as 'processing' first, so a crash leaves an inspectable state."""
    with unit_of_work(db):
        job = db.scalar(select(DocumentProcessingJob).where(DocumentProcessingJob.id == job_id).with_for_update())
        if job is None:
            raise not_found("Processing job")
        if job.status not in ("queued", "failed"):
            raise rule("job_not_runnable", "This job has already been processed. Correct it or reject it.")
        job.status = "processing"
        job.provider_name = provider.name[:60]

    try:
        content, content_type = read_content()
        result = provider.extract(content, content_type)
    except Exception:  # noqa: BLE001 - any provider or storage failure becomes a generic, safe state
        with unit_of_work(db):
            job = get_job(db, job_id)
            job.status = "failed"
            job.issues = [ex.Issue("document", "provider_error", "Processing failed. Try again or enter the details manually.").as_dict()]
            audit(db, request, auth, "document_processing.failed", "document_processing_job", str(job.id)[:36])
        return job

    with unit_of_work(db):
        job = get_job(db, job_id)
        job.raw_extraction = _json_safe(result.raw)
        job.provider_available = result.available
        if not result.available:
            job.status = "needs_review"
            job.current_payload = {}
            job.field_confidence = {}
            job.classification = None
            job.classification_confidence = None
            job.issues = [
                ex.Issue(
                    "document",
                    "no_extraction_provider",
                    "No OCR or document AI is configured. Enter or correct the details manually.",
                ).as_dict()
            ]
        else:
            values, confidences = ex.payload_from_provider(result)
            classification = result.classification if result.classification in ex.DOCUMENT_TYPES else "other"
            class_conf = result.classification_confidence
            if class_conf is not None and not (Decimal("0") <= class_conf <= Decimal("1")):
                class_conf = Decimal("0")
            outcome = _with_duplicates(db, job, ex.validate_payload(classification, values, confidences, class_conf),
                                       values, classification)
            job.status = outcome.status
            job.classification = outcome.classification
            job.classification_confidence = class_conf
            job.current_payload = _json_safe(values)
            job.field_confidence = {name: str(value) for name, value in confidences.items()}
            job.issues = [issue.as_dict() for issue in outcome.issues]
        job.reviewed_at = None
        audit(
            db,
            request,
            auth,
            "document_processing.extracted",
            "document_processing_job",
            str(job.id)[:36],
            {"provider": provider.name, "status": job.status, "issues": len(job.issues)},
        )
    return job


def _file_hash(db: DBSession, document_id: UUID) -> str | None:
    return db.scalar(select(DocumentFile.sha256).where(DocumentFile.document_id == document_id).limit(1))


def _fingerprint(db: DBSession, other: DocumentProcessingJob) -> ex.Fingerprint:
    payload = dict(other.current_payload or {})
    party = str(payload.get("party_id") or payload.get("party_name") or "").strip().lower() or None
    reference = str(payload.get("reference_number") or "").strip().lower() or None
    return ex.Fingerprint(str(other.id), _file_hash(db, other.document_id), party, reference)


def _with_duplicates(db: DBSession, job: DocumentProcessingJob, outcome: ex.ValidationOutcome,
                     payload: dict[str, Any], classification: str) -> ex.ValidationOutcome:
    """Adds duplicate-file and duplicate-reference warnings for the payload being validated. Jobs still queued,
    processing or failed are not compared."""
    payload = dict(payload)
    party = str(payload.get("party_id") or payload.get("party_name") or "").strip().lower() or None
    earlier = [
        _fingerprint(db, other)
        for other in db.scalars(select(DocumentProcessingJob).where(
            DocumentProcessingJob.id != job.id,
            DocumentProcessingJob.status.not_in(("queued", "processing", "failed")),
        ))
    ]
    duplicates = ex.duplicate_issues(classification, payload, _file_hash(db, job.document_id), party, earlier)
    if not duplicates:
        return outcome
    issues = list(outcome.issues) + duplicates
    return dataclasses.replace(outcome, status="needs_review", issues=issues)


def _revalidate(db: DBSession, job: DocumentProcessingJob) -> ex.ValidationOutcome:
    confidences = {name: Decimal(str(value)) for name, value in (job.field_confidence or {}).items()}
    class_conf = job.classification_confidence
    base = ex.validate_payload(job.classification or "other", dict(job.current_payload or {}), confidences, class_conf)
    return _with_duplicates(db, job, base, dict(job.current_payload or {}), job.classification or "other")


def correct_job(
    db: DBSession, request: Request, auth: AuthenticatedSession, job_id: UUID, changes: dict[str, Any]
) -> DocumentProcessingJob:
    """Owner correction. Every corrected field becomes owner-confirmed (confidence 1). The job is re-validated."""
    if set(changes) - CORRECTABLE_FIELDS:
        raise rule("invalid_field", "Unknown field: " + ", ".join(sorted(set(changes) - CORRECTABLE_FIELDS)))
    with unit_of_work(db):
        job = db.scalar(select(DocumentProcessingJob).where(DocumentProcessingJob.id == job_id).with_for_update())
        if job is None:
            raise not_found("Processing job")
        if job.status not in ("needs_review", "ready"):
            raise rule("job_closed", "Only jobs awaiting review can be corrected")
        payload = dict(job.current_payload or {})
        confidence = dict(job.field_confidence or {})
        for key, value in changes.items():
            if key == "document_type":
                if value not in ex.DOCUMENT_TYPES:
                    raise rule("invalid_type", "Unknown document type")
                job.classification = value
                job.classification_confidence = Decimal("1")
            elif key == "party_id":
                if value is None:
                    payload.pop("party_id", None)
                else:
                    party = catalog.get_party(db, value)
                    payload["party_id"] = str(party.id)
                    payload["party_name"] = party.name
            elif key == "lines":
                if not isinstance(value, list):
                    raise rule("invalid_lines", "Lines must be a list")
                payload["lines"] = _json_safe(value)
            else:
                payload[key] = _json_safe(value)
            confidence[key] = "1"
        job.current_payload = _json_safe(payload)
        job.field_confidence = confidence
        outcome = _revalidate(db, job)
        job.status = outcome.status
        job.issues = [issue.as_dict() for issue in outcome.issues]
        job.reviewed_at = utcnow()
        audit(
            db,
            request,
            auth,
            "extraction_corrected",
            "document_processing_job",
            str(job.id)[:36],
            {"changed_fields": ",".join(sorted(changes)), "status": job.status},
        )
    return job


def reject_job(db: DBSession, request: Request, auth: AuthenticatedSession, job_id: UUID, reason: str) -> DocumentProcessingJob:
    with unit_of_work(db):
        job = db.scalar(select(DocumentProcessingJob).where(DocumentProcessingJob.id == job_id).with_for_update())
        if job is None:
            raise not_found("Processing job")
        if job.status in ("approved", "rejected", "processing"):
            raise rule("job_closed", "This job can no longer be rejected")
        job.status = "rejected"
        job.decided_at = utcnow()
        audit(db, request, auth, "document_processing.rejected", "document_processing_job", str(job.id)[:36], {"reason": reason[:120]})
    return job


def _lines_for_builder(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lines = payload.get("lines") or []
    built = []
    for item in lines:
        built.append(
            {
                "product_id": item.get("product_id"),
                "expense_account_id": item.get("expense_account_id"),
                "description": item.get("description"),
                "quantity": item.get("quantity"),
                "rate": item.get("rate"),
                "discount_percent": item.get("discount_percent"),
                "tax_rate": item.get("tax_rate"),
            }
        )
    return built


FINANCE_TARGETS = frozenset({"emi", "interest", "salary", "debt", "recurring_payment"})


def _approve_finance_job(db, request, auth, job, target, payload, outcome):
    """Finance documents create a debt or a payment obligation, never stock or a journal entry. Confidence and
    validation are checked again here. Anything not ready raises and nothing is saved."""
    from decimal import Decimal

    from app.finance.approval import DEBT_DOCUMENTS, draft_financial_record
    from app.services import finance as finance_service

    confidences = {name: Decimal(value) for name, value in outcome.field_confidence.items()}
    result = draft_financial_record(target, payload, confidences, None)
    if result.status != "ready":
        raise rule("validation_failed", "Fix these first: " + "; ".join(i["message"] for i in result.issues[:5]))
    if target in DEBT_DOCUMENTS:
        created = finance_service.create_debt_from_draft(db, request, auth, result.debt, job.id)
    else:
        created = finance_service.create_obligation_from_draft(db, request, auth, result.obligation, job.id)
    job.status = "approved"
    job.approved_document_type = target
    job.approved_document_id = created.id
    job.review_acknowledged = bool(outcome.issues)
    job.decided_at = utcnow()
    audit(db, request, auth, "extraction_approved", "document_processing_job", str(job.id)[:36],
          {"target": target, "record": str(created.id), "acknowledged_warnings": str(bool(outcome.issues))})
    return job, target, created.id


def approve_job(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    job_id: UUID,
    *,
    acknowledge_warnings: bool,
) -> tuple[DocumentProcessingJob, str, UUID]:
    """Create one DRAFT business document from the reviewed payload. Stock and the ledger are not touched."""
    with unit_of_work(db):
        job = db.scalar(select(DocumentProcessingJob).where(DocumentProcessingJob.id == job_id).with_for_update())
        if job is None:
            raise not_found("Processing job")
        if job.status not in ("needs_review", "ready"):
            raise rule("job_closed", "This job cannot be approved in its current state")
        target = job.classification or "other"
        if target not in ex.DRAFT_TARGETS:
            raise rule("review_only_type", "This document type is kept for review only. No draft can be created from it.")
        outcome = _revalidate(db, job)
        blocking = [issue for issue in outcome.issues if issue.code not in ACKNOWLEDGEABLE_CODES]
        if blocking:
            raise rule("validation_failed", "Fix these first: " + "; ".join(issue.message for issue in blocking[:5]))
        if outcome.issues and not acknowledge_warnings:
            raise rule("review_required", "Confirm the flagged values, or acknowledge the remaining warnings.")

        payload = dict(job.current_payload or {})
        if target in FINANCE_TARGETS:
            return _approve_finance_job(db, request, auth, job, target, payload, outcome)
        lines = _lines_for_builder(payload)
        party_id = UUID(str(payload["party_id"]))
        document_date = date.fromisoformat(str(payload["document_date"]))
        due = payload.get("due_date")
        due_date = date.fromisoformat(str(due)) if due else None

        if target == "purchase_bill":
            created = build_purchase_bill(
                db,
                party_id=party_id,
                supplier_invoice_number=str(payload["reference_number"]),
                bill_date=document_date,
                due_date=due_date,
                purchase_order_id=None,
                notes=payload.get("notes"),
                lines=lines,
                owner_id=auth.owner.id,
            )
            number = created.number
        elif target == "purchase_order":
            created = build_purchase_order(
                db,
                party_id=party_id,
                order_date=document_date,
                expected_date=due_date,
                purchase_request_id=None,
                notes=payload.get("notes"),
                lines=lines,
                owner_id=auth.owner.id,
            )
            number = created.number
        elif target == "quotation":
            created = build_quotation(
                db,
                party_id=party_id,
                quotation_date=document_date,
                valid_until=due_date,
                notes=payload.get("notes"),
                lines=lines,
                owner_id=auth.owner.id,
            )
            number = created.number
        else:  # sales_order
            created = _build_sales_order(
                db,
                party_id=party_id,
                order_date=document_date,
                notes=payload.get("notes"),
                quotation_id=None,
                lines=lines,
                owner_id=auth.owner.id,
            )
            number = created.number

        job.status = "approved"
        job.approved_document_type = target
        job.approved_document_id = created.id
        job.review_acknowledged = bool(outcome.issues)
        job.decided_at = utcnow()
        audit(
            db,
            request,
            auth,
            "extraction_approved",
            "document_processing_job",
            str(job.id)[:36],
            {"target": target, "draft": number, "acknowledged_warnings": str(bool(outcome.issues))},
        )
    return job, target, created.id


def job_summary(job: DocumentProcessingJob) -> dict[str, Any]:
    """Safe view of a job for the API. The raw provider evidence is included only by the detail endpoint."""
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "classification": job.classification,
        "classification_confidence": job.classification_confidence,
        "provider_name": job.provider_name,
        "provider_available": job.provider_available,
        "issues": job.issues or [],
        "approved_document_type": job.approved_document_type,
        "approved_document_id": job.approved_document_id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
