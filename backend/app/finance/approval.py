"""Turns a reviewed finance document into a draft financial record.

Covered documents: emi, interest, salary (payment obligation), debt (debt + schedule source) and recurring_payment
(recurring payment obligation). Every document must pass the same deterministic validation as the rest of the system
(required fields, positive amount, dates, confidence threshold). Anything else returns needs_review with its issues.

Nothing is saved here. The caller (app/services/finance.py) stores the draft inside a transaction, linked to the job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.commerce.domain import parse_iso_date
from app.commerce.extraction import validate_payload
from app.finance.domain import (
    CRITICAL_KINDS,
    FREQUENCIES,
    INTEREST_TYPES,
    MAX_MONTHS,
    OBLIGATION_KINDS,
    FinanceRuleViolation,
    add_months,
    parse_money,
    parse_rate,
)

OBLIGATION_DOCUMENTS = ("emi", "interest", "salary", "recurring_payment")
DEBT_DOCUMENTS = ("debt",)
FINANCE_DOCUMENTS = OBLIGATION_DOCUMENTS + DEBT_DOCUMENTS


@dataclass(frozen=True)
class ObligationDraft:
    kind: str
    title: str
    counterparty: str
    amount: Decimal
    frequency: str
    first_due_date: date
    is_critical: bool
    reference: str


@dataclass(frozen=True)
class DebtDraft:
    name: str
    lender: str
    principal: Decimal
    annual_rate: Decimal
    interest_type: str
    tenure_months: int
    start_date: date
    first_due_date: date
    reference: str


@dataclass(frozen=True)
class ApprovalResult:
    status: str  # "ready" | "needs_review"
    obligation: ObligationDraft | None
    debt: DebtDraft | None
    issues: list[dict[str, str]]


def _review(outcome_issues: list[dict[str, str]]) -> ApprovalResult:
    return ApprovalResult("needs_review", None, None, outcome_issues)


def draft_financial_record(
    document_type: str,
    payload: dict[str, Any],
    field_confidence: dict[str, Decimal] | None = None,
    classification_confidence: Decimal | None = None,
) -> ApprovalResult:
    if document_type not in FINANCE_DOCUMENTS:
        raise FinanceRuleViolation("not_a_finance_document", f"{document_type} does not create a financial record")
    outcome = validate_payload(document_type, payload, field_confidence, classification_confidence)
    if outcome.status != "ready":
        return _review([issue.as_dict() for issue in outcome.issues])

    party = str(payload["party_name"]).strip()
    reference = str(payload.get("reference_number") or "").strip()
    amount = parse_money(str(payload["grand_total"]), "grand_total")
    document_day = date.fromisoformat(str(payload["document_date"]))

    if document_type == "debt":
        rate = parse_rate(str(payload["interest_rate"]), "interest_rate")
        try:
            tenure = int(str(payload["tenure_months"]))
        except ValueError:
            return _review([{"field": "tenure_months", "code": "invalid_tenure", "message": "Tenure must be a whole number of months"}])
        if not 1 <= tenure <= MAX_MONTHS:
            return _review([{"field": "tenure_months", "code": "invalid_tenure", "message": f"Tenure must be 1-{MAX_MONTHS} months"}])
        interest_type = str(payload.get("interest_type") or "reducing").strip().lower()
        if interest_type not in INTEREST_TYPES:
            return _review([{"field": "interest_type", "code": "invalid_interest_type", "message": "Interest type must be reducing or flat"}])
        first_due = add_months(document_day, 1)
        if payload.get("due_date"):
            parse_iso_date(payload["due_date"], "due_date")  # validated; raises on a bad format
            first_due = date.fromisoformat(str(payload["due_date"]))
        debt = DebtDraft(f"{party} loan {reference}".strip(), party, amount, rate, interest_type, tenure, document_day, first_due, reference)
        return ApprovalResult("ready", None, debt, [])

    due = date.fromisoformat(str(payload.get("due_date") or payload["document_date"]))
    if document_type == "salary":
        draft = ObligationDraft("salary", f"Salary - {party}", party, amount, "monthly", due, True, reference)
    elif document_type == "emi":
        draft = ObligationDraft("emi", f"EMI {reference} - {party}".strip(), party, amount, "once", due, True, reference)
    elif document_type == "interest":
        draft = ObligationDraft("interest", f"Interest - {party}", party, amount, "once", due, True, reference)
    else:  # recurring_payment
        kind = str(payload.get("recurring_kind") or "other").strip()
        frequency = str(payload.get("frequency") or "monthly").strip()
        if kind not in OBLIGATION_KINDS:
            return _review([{"field": "recurring_kind", "code": "invalid_kind", "message": "Unknown recurring payment kind"}])
        if frequency not in FREQUENCIES:
            return _review([{"field": "frequency", "code": "invalid_frequency", "message": "Unknown frequency"}])
        draft = ObligationDraft(kind, f"{kind.replace('_', ' ').title()} - {party}", party, amount, frequency, due,
                                kind in CRITICAL_KINDS, reference)
    return ApprovalResult("ready", draft, None, [])


# Name used by the earlier obligation tests and callers.
draft_obligation = draft_financial_record
