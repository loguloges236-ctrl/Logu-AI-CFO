"""Request bodies for the finance API. Money and rates arrive as strings and are parsed with Decimal in the services."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MONEY_PATTERN = r"^\d{1,16}(\.\d{1,2})?$"
RATE_PATTERN = r"^\d{1,3}(\.\d{1,4})?$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DebtIn(_Strict):
    name: str = Field(min_length=1, max_length=120)
    lender: str = Field(min_length=1, max_length=120)
    principal: str = Field(pattern=MONEY_PATTERN)
    annual_rate: str = Field(pattern=RATE_PATTERN)
    interest_type: str = Field(default="reducing", pattern="^(reducing|flat)$")
    tenure_months: int = Field(ge=1, le=600)
    start_date: date
    first_due_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class ObligationIn(_Strict):
    kind: str = Field(pattern="^(rent|emi|insurance|subscription|supplier_settlement|credit_card|utilities|salary|interest|other)$")
    title: str = Field(min_length=1, max_length=160)
    counterparty: str | None = Field(default=None, max_length=160)
    amount: str = Field(pattern=MONEY_PATTERN)
    frequency: str = Field(pattern="^(once|weekly|monthly|yearly)$")
    first_due_date: date
    end_date: date | None = None


class PaymentIn(_Strict):
    period_due_date: date
    amount: str = Field(pattern=MONEY_PATTERN)
    paid_on: date
    cash_account_id: UUID
    debit_account_id: UUID
    reference: str | None = Field(default=None, max_length=80)


class ReverseIn(_Strict):
    paid_on: date


class SettlementIn(_Strict):
    direction: str = Field(pattern="^(receivable|payable)$")
    document_id: UUID
    amount: str = Field(pattern=MONEY_PATTERN)
    settled_on: date
    method: str = Field(pattern="^(cash|bank|upi|cheque|card|other)$")
    reference: str = Field(min_length=1, max_length=80)
    cash_account_id: UUID
    control_account_id: UUID


class SettlementReverseIn(_Strict):
    reversed_on: date
    reason: str = Field(min_length=1, max_length=200)


class LedgerLinkIn(_Strict):
    ledger_account_id: UUID


class ImportWindowIn(_Strict):
    since: date
    until: date


class ReconcileIn(_Strict):
    bank_account_id: UUID
    since: date
    until: date


class DecisionIn(_Strict):
    decision: str = Field(pattern="^(confirmed|rejected)$")


class AskIn(_Strict):
    question: str = Field(min_length=1, max_length=300)
