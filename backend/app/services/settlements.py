"""Settlement service: receivable receipts and payable payments against posted documents, with the journal entry.

An approval is one owner action. In one transaction it:
  1. locks the document row (serialises settlements on the same document),
  2. checks the net settled amount stays within the document total (settlement_status + database trigger),
  3. creates and posts the balanced journal through the Phase 2 accounting service,
  4. inserts the settlement row with journal_entry_id set,
  5. writes an audit event.
Any failure rolls the whole thing back, so there is never a settlement without its journal, or a journal without its row.

Idempotency: `reference` is required and is the replay key for (direction, document, amount, date). Submitting the same
settlement twice returns the original row and posts nothing new. A changed amount or date with the same reference is a
conflict, not a replay.

Reversal creates a compensating journal (accounting.reverse_entry) and a negative settlement row that points at the
original. Neither the original journal nor the original settlement is changed or deleted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.finance.domain import FinanceRuleViolation, parse_money
from app.models.accounting import Account
from app.models.commerce import PurchaseBill, SalesInvoice
from app.models.finance import Settlement, SettlementRequest
from app.services import accounting as acc
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, unit_of_work
from app.settlements.domain import settlement_status
from app.settlements.posting import settlement_journal

METHODS = ("cash", "bank", "upi", "cheque", "card", "other")
MAX_REFERENCE = 80


def _rule(code: str, message: str) -> FinanceRuleViolation:
    return FinanceRuleViolation(code, message)


def _document(db: DBSession, direction: str, document_id: UUID, *, lock: bool) -> SalesInvoice | PurchaseBill:
    model = SalesInvoice if direction == "receivable" else PurchaseBill
    query = select(model).where(model.id == document_id)
    if lock:
        query = query.with_for_update()
    doc = db.scalar(query)
    if doc is None:
        raise _rule("not_found", "Document was not found")
    if doc.status != "posted":
        raise _rule("not_posted", "Only posted documents can be settled")
    return doc


def _settled(db: DBSession, direction: str, document_id: UUID) -> Decimal:
    column = Settlement.sales_invoice_id if direction == "receivable" else Settlement.purchase_bill_id
    total = db.scalar(select(func.coalesce(func.sum(Settlement.amount), 0)).where(
        Settlement.direction == direction, column == document_id))
    return Decimal(total or 0)


def _control_account(db: DBSession, account_id: UUID, expected_subtype: str) -> None:
    account = db.get(Account, account_id)
    if account is None or not account.is_active:
        raise _rule("not_found", "Account was not found or is inactive")
    if expected_subtype == "cash" and account.subtype not in ("cash", "bank", "petty_cash", "other_financial"):
        raise _rule("invalid_cash_account", "Cash side must be a cash or bank account")


def _validate_input(direction: str, method: str, reference: str, amount: str) -> tuple[str, Decimal]:
    if direction not in ("receivable", "payable"):
        raise _rule("invalid_direction", "Direction must be receivable or payable")
    if method not in METHODS:
        raise _rule("invalid_method", "Unknown payment method")
    reference = (reference or "").strip()
    if not reference or len(reference) > MAX_REFERENCE:
        raise _rule("invalid_reference", f"Reference is required (1-{MAX_REFERENCE} characters)")
    value = parse_money(amount, "amount")
    if value <= 0:
        raise _rule("invalid_amount", "Settlement amount must be greater than zero (use reversal to undo)")
    return reference, value


def _check_accounts(db: DBSession, cash_account_id: UUID, control_account_id: UUID) -> None:
    _control_account(db, cash_account_id, "cash")
    control = db.get(Account, control_account_id)
    if control is None or not control.is_active:
        raise _rule("not_found", "Control account was not found or is inactive")


def _approve_locked(db: DBSession, request: Request, auth: AuthenticatedSession, *, direction: str, document_id: UUID,
                    value: Decimal, settled_on: date, method: str, reference: str, cash_account_id: UUID,
                    control_account_id: UUID) -> Settlement:
    """The approval core. Must run inside a unit of work; it never commits by itself."""
    doc = _document(db, direction, document_id, lock=True)
    column = Settlement.sales_invoice_id if direction == "receivable" else Settlement.purchase_bill_id
    existing = db.scalar(select(Settlement).where(
        Settlement.direction == direction, column == document_id,
        Settlement.reference == reference, Settlement.reverses_settlement_id.is_(None),
    ))
    if existing is not None:
        if existing.amount == value and existing.settled_on == settled_on and existing.method == method:
            return existing
        raise _rule("reference_reused", "This reference was already used for a different settlement")

    settlement_status(doc.grand_total, _settled(db, direction, document_id) + value)

    settlement_id = uuid.uuid4()
    lines = settlement_journal(direction, value, cash_account_id, control_account_id)
    draft = acc.create_draft(
        db, request, auth,
        acc.EntryDraftInput(
            transaction_date=settled_on,
            description=f"Settlement {reference} ({direction}) for {doc.number}"[:500],
            reference_type="settlement",
            reference_id=str(settlement_id),
            lines=[acc.LineInput(account_id=line.account_id, debit=str(line.debit), credit=str(line.credit))
                   for line in lines],
        ),
        commit=False,
    )
    posted = acc.post_entry(db, request, auth, draft.id, posting_date=settled_on, commit=False)
    row = Settlement(
        id=settlement_id, direction=direction,
        sales_invoice_id=doc.id if direction == "receivable" else None,
        purchase_bill_id=doc.id if direction == "payable" else None,
        amount=value, settled_on=settled_on, method=method, reference=reference,
        journal_entry_id=posted.id,
    )
    db.add(row)
    db.flush()  # the database cap and journal-uniqueness rules are checked here
    audit(db, request, auth, "settlement.approved", "settlement", str(row.id), {
        "direction": direction, "document": doc.number, "amount": str(value), "journal_entry": str(posted.id),
    })
    return row


def approve_settlement(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    direction: str,
    document_id: UUID,
    amount: str,
    settled_on: date,
    method: str,
    reference: str,
    cash_account_id: UUID,
    control_account_id: UUID,
) -> Settlement:
    reference, value = _validate_input(direction, method, reference, amount)
    _check_accounts(db, cash_account_id, control_account_id)
    with unit_of_work(db):
        return _approve_locked(db, request, auth, direction=direction, document_id=document_id, value=value,
                               settled_on=settled_on, method=method, reference=reference,
                               cash_account_id=cash_account_id, control_account_id=control_account_id)


# ---------------------------------------------------------------- lifecycle: request -> approve or reject

def request_settlement(db: DBSession, request: Request, auth: AuthenticatedSession, *, direction: str, document_id: UUID,
                       amount: str, settled_on: date, method: str, reference: str, cash_account_id: UUID,
                       control_account_id: UUID) -> SettlementRequest:
    """Creates a pending request. Nothing is posted. The amount is checked against the outstanding balance now and again
    at approval, because other settlements can land in between."""
    reference, value = _validate_input(direction, method, reference, amount)
    _check_accounts(db, cash_account_id, control_account_id)
    with unit_of_work(db):
        doc = _document(db, direction, document_id, lock=True)
        settlement_status(doc.grand_total, _settled(db, direction, document_id) + value)
        column = SettlementRequest.sales_invoice_id if direction == "receivable" else SettlementRequest.purchase_bill_id
        same = db.scalar(select(SettlementRequest).where(
            SettlementRequest.direction == direction, column == document_id, SettlementRequest.reference == reference))
        if same is not None:
            if same.status == "pending" and same.amount == value and same.settled_on == settled_on and same.method == method:
                return same  # retried request: return the pending one, do not create a second
            raise _rule("reference_reused", "This reference is already used for another request")
        row = SettlementRequest(
            direction=direction,
            sales_invoice_id=doc.id if direction == "receivable" else None,
            purchase_bill_id=doc.id if direction == "payable" else None,
            amount=value, settled_on=settled_on, method=method, reference=reference,
            cash_account_id=cash_account_id, control_account_id=control_account_id, status="pending",
        )
        db.add(row)
        db.flush()
        audit(db, request, auth, "settlement.requested", "settlement_request", str(row.id), {
            "direction": direction, "document": doc.number, "amount": str(value)})
    return row


def _request_for_update(db: DBSession, request_id: UUID) -> SettlementRequest:
    row = db.scalar(select(SettlementRequest).where(SettlementRequest.id == request_id).with_for_update())
    if row is None:
        raise _rule("not_found", "Settlement request was not found")
    return row


def approve_settlement_request(db: DBSession, request: Request, auth: AuthenticatedSession, request_id: UUID) -> Settlement:
    """Approves a pending request: creates the settlement and its journal in the same transaction. Approving an
    already-approved request returns its settlement (replay). A rejected request cannot be approved."""
    with unit_of_work(db):
        req = _request_for_update(db, request_id)
        if req.status == "approved":
            return db.get(Settlement, req.settlement_id)
        if req.status != "pending":
            raise _rule("already_decided", "This request was already rejected")
        settlement = _approve_locked(
            db, request, auth,
            direction=req.direction,
            document_id=req.sales_invoice_id or req.purchase_bill_id,
            value=req.amount, settled_on=req.settled_on, method=req.method, reference=req.reference,
            cash_account_id=req.cash_account_id, control_account_id=req.control_account_id,
        )
        req.status = "approved"
        req.settlement_id = settlement.id
        req.decided_at = datetime.now(UTC)
        db.flush()
        audit(db, request, auth, "settlement.request_approved", "settlement_request", str(req.id), {
            "settlement": str(settlement.id)})
    return settlement


def reject_settlement_request(db: DBSession, request: Request, auth: AuthenticatedSession, request_id: UUID) -> SettlementRequest:
    with unit_of_work(db):
        req = _request_for_update(db, request_id)
        if req.status == "rejected":
            return req
        if req.status != "pending":
            raise _rule("already_decided", "This request was already approved")
        req.status = "rejected"
        req.decided_at = datetime.now(UTC)
        db.flush()
        audit(db, request, auth, "settlement.request_rejected", "settlement_request", str(req.id), {})
    return req


def list_requests(db: DBSession, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if status is not None and status not in ("pending", "approved", "rejected"):
        raise _rule("invalid_status", "Status must be pending, approved or rejected")
    query = select(SettlementRequest).order_by(SettlementRequest.created_at.desc()).limit(min(limit, 200))
    if status is not None:
        query = query.where(SettlementRequest.status == status)
    return [{"id": str(r.id), "direction": r.direction, "amount": str(r.amount), "settled_on": r.settled_on.isoformat(),
             "method": r.method, "reference": r.reference, "status": r.status,
             "settlement_id": str(r.settlement_id) if r.settlement_id else None} for r in db.scalars(query)]


def reverse_settlement(db: DBSession, request: Request, auth: AuthenticatedSession, settlement_id: UUID, *,
                       reversed_on: date, reason: str) -> Settlement:
    reason = (reason or "").strip()
    if not reason or len(reason) > 200:
        raise _rule("invalid_reason", "A reason is required (1-200 characters)")
    with unit_of_work(db):
        original = db.scalar(select(Settlement).where(Settlement.id == settlement_id))
        if original is None:
            raise _rule("not_found", "Settlement was not found")
        if original.amount <= 0 or original.reverses_settlement_id is not None:
            raise _rule("not_reversible", "Only an original settlement can be reversed")
        doc = _document(db, original.direction, original.sales_invoice_id or original.purchase_bill_id, lock=True)
        already = db.scalar(select(Settlement.id).where(Settlement.reverses_settlement_id == original.id).limit(1))
        if already is not None:
            raise _rule("already_reversed", "This settlement has already been reversed")
        if original.journal_entry_id is None:
            raise _rule("not_posted", "The original settlement has no journal entry to reverse")

        reversal_entry = acc.reverse_entry(db, request, auth, original.journal_entry_id,
                                           posting_date=reversed_on,
                                           description=f"Reversal of settlement {original.reference}: {reason}",
                                           commit=False)
        row = Settlement(
            direction=original.direction,
            sales_invoice_id=original.sales_invoice_id,
            purchase_bill_id=original.purchase_bill_id,
            amount=-original.amount,
            settled_on=reversed_on,
            method=original.method,
            reference=f"REV {original.reference}"[:MAX_REFERENCE],
            journal_entry_id=reversal_entry.id,
            reverses_settlement_id=original.id,
        )
        db.add(row)
        db.flush()
        audit(db, request, auth, "settlement.reversed", "settlement", str(row.id), {
            "original": str(original.id), "reversal_journal": str(reversal_entry.id),
            "document": doc.number, "reason": reason,
        })
    return row


def list_settlements(db: DBSession, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(Settlement).order_by(Settlement.created_at.desc()).limit(min(limit, 200))))
    out = []
    for row in rows:
        if row.reverses_settlement_id is not None:
            state = "reversal"
        elif db.scalar(select(Settlement.id).where(Settlement.reverses_settlement_id == row.id).limit(1)) is not None:
            state = "reversed"
        else:
            state = "completed"
        out.append({
            "id": str(row.id), "direction": row.direction, "amount": str(row.amount),
            "settled_on": row.settled_on.isoformat(), "method": row.method, "reference": row.reference,
            "journal_entry_id": str(row.journal_entry_id) if row.journal_entry_id else None, "state": state,
            "reverses": str(row.reverses_settlement_id) if row.reverses_settlement_id else None,
        })
    return out

