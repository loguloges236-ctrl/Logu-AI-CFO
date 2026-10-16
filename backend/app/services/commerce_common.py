"""Shared Phase 3 service helpers: one business event per transaction, document numbering, audit and errors.

Rules used by every commerce service:
- A business event (a GRN, a bill post, an invoice post, a void) runs inside `unit_of_work`. Stock movements,
  document status and journal lines commit together, or the whole event rolls back.
- Internal `build_*` / `_move_*` helpers never commit. Only public functions open a unit of work.
- Document numbers come from PostgreSQL sequences, so they are never reused.
- Every change writes an audit row through the Phase 1 audit service.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DBSession

from app.commerce.domain import CommerceRuleViolation
from app.services.audit import write_audit
from app.services.auth import AuthenticatedSession

# Only these fixed names are ever interpolated into SQL. They are not user input.
_NUMBER_SEQUENCES: dict[str, tuple[str, str]] = {
    "purchase_request": ("commerce_purchase_request_seq", "PR"),
    "purchase_order": ("commerce_purchase_order_seq", "PO"),
    "goods_receipt": ("commerce_goods_receipt_seq", "GRN"),
    "purchase_bill": ("commerce_purchase_bill_seq", "BILL"),
    "quotation": ("commerce_quotation_seq", "QTN"),
    "sales_order": ("commerce_sales_order_seq", "SO"),
    "sales_invoice": ("commerce_sales_invoice_seq", "INV"),
}


def rule(code: str, message: str) -> CommerceRuleViolation:
    return CommerceRuleViolation(code, message)


def not_found(what: str) -> CommerceRuleViolation:
    return CommerceRuleViolation("not_found", f"{what} was not found")


@contextmanager
def unit_of_work(db: DBSession) -> Iterator[None]:
    """Run one business event atomically. Any failure rolls back everything, including journal lines."""
    try:
        yield
        try:
            db.commit()
        except DBAPIError:
            db.rollback()
            raise rule("database_rule", "The database rejected this change. Nothing was recorded.") from None
    except BaseException:
        db.rollback()
        raise


def flush(db: DBSession) -> None:
    try:
        db.flush()
    except DBAPIError:
        db.rollback()
        raise rule("database_rule", "The database rejected this change. Nothing was recorded.") from None


def next_number(db: DBSession, kind: str) -> str:
    sequence, prefix = _NUMBER_SEQUENCES[kind]
    value = db.scalar(text(f"SELECT nextval('{sequence}')"))
    return f"{prefix}-{int(value or 0):06d}"


def audit(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    write_audit(
        db,
        request,
        action=action,
        success=True,
        owner_id=auth.owner.id,
        session_id=auth.session.id,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )


def discard_draft(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    header: Any,
    line_model: Any,
    line_column: Any,
    kind: str,
) -> None:
    """Remove a draft that was never posted. Posted and voided history is never removed (the database refuses it)."""
    from sqlalchemy import delete

    if header.status != "draft":
        raise rule("not_draft", "Only draft documents can be discarded. Void a posted document instead.")
    with unit_of_work(db):
        db.execute(delete(line_model).where(line_column == header.id))
        flush(db)
        db.delete(header)
        audit(db, request, auth, f"{kind}.draft_discarded", kind, str(header.id))
