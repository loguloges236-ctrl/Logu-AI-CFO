"""Finance ORM models: debts, EMI schedule, payment obligations, payment records, read-only bank data,
reconciliation suggestions and notifications.

The explicit migration alembic/versions/20261008_0004_finance_bank.py defines the schema, and the database
triggers enforce the append-only and guard rules. This metadata must match the migration; the static test
tests/unit/test_finance_static.py compares table and column names with the migration text.
Money is NUMERIC(18,2) and rates NUMERIC(7,4). Floats are never used.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CHAR,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MONEY = Numeric(18, 2)
RATE = Numeric(7, 4)


class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lender: Mapped[str] = mapped_column(String(120), nullable=False)
    principal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    annual_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    interest_type: Mapped[str] = mapped_column(String(10), nullable=False)
    emi: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tenure_months: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")
    loan_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    notes: Mapped[str | None] = mapped_column(String(500))
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_processing_jobs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DebtScheduleItem(Base):
    __tablename__ = "debt_schedule_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    debt_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("debts.id", ondelete="RESTRICT"), nullable=False)
    installment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    interest_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    emi_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class PaymentObligation(Base):
    __tablename__ = "payment_obligations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="INR")
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    first_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")
    debt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("debts.id", ondelete="RESTRICT"))
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_processing_jobs.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentRecord(Base):
    """Append-only, signed. A reversal is a new negative row that points at the row it reverses."""

    __tablename__ = "payment_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payment_obligations.id", ondelete="RESTRICT"), nullable=False
    )
    period_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80))
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    reverses_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payment_records.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BankAccount(Base):
    """Read-only mirror of an external account. Nothing here can move money."""

    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    ledger_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    provider_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    balance_as_of: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BankTransaction(Base):
    """Append-only imported bank line. Signed: credits positive, debits negative."""

    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    balance_after: Mapped[Decimal | None] = mapped_column(MONEY)
    import_key: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReconciliationSuggestion(Base):
    """Immutable suggestion. Only `status` and `decided_at` may change, enforced by a trigger."""

    __tablename__ = "reconciliation_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("bank_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="suggested")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Notification(Base):
    """Only `read_at` may change. Rows are never deleted. dedupe_key is unique."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(30), nullable=False, server_default="in_app")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Settlement(Base):
    """Append-only receivable or payable settlement. A reversal is a negative row that references the original."""

    __tablename__ = "settlements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    sales_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sales_invoices.id", ondelete="RESTRICT")
    )
    purchase_bill_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_bills.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80))
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    reverses_settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("settlements.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SettlementRequest(Base):
    """Pending, approved or rejected settlement request. A decided request is final (database trigger)."""

    __tablename__ = "settlement_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    sales_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sales_invoices.id", ondelete="RESTRICT")
    )
    purchase_bill_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_bills.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    cash_account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    control_account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("settlements.id", ondelete="RESTRICT"), unique=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
