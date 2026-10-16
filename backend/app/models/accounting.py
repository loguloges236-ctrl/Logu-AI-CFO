"""Accounting ORM models. The explicit migration 20261008_0002 must match this metadata.

Money columns are NUMERIC(18,2). Immutability of posted entries, closed-period protection, the
balanced-entry rule and account immutability are enforced by database triggers defined only in the
migration (see alembic/versions/20261008_0002_accounting.py). The application checks the same rules
first so that clients get a clear error rather than a database exception.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.accounting.domain import ACCOUNT_SUBTYPES, AccountCategory, EntryStatus, PeriodStatus
from app.db.base import Base
from app.models.entities import TimestampMixin

MONEY = Numeric(18, 2)


def _in_values(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("code ~ '^[0-9]{4,10}$'", name="ck_accounts_code_format"),
        CheckConstraint(
            _in_values("category", [c.value for c in AccountCategory]), name="ck_accounts_category"
        ),
        CheckConstraint(_in_values("subtype", sorted(ACCOUNT_SUBTYPES)), name="ck_accounts_subtype"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_accounts_parent_not_self"),
        Index("ix_accounts_category", "category"),
        Index("ix_accounts_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    subtype: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class AccountingPeriod(TimestampMixin, Base):
    __tablename__ = "accounting_periods"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_periods_dates"),
        CheckConstraint(_in_values("status", [s.value for s in PeriodStatus]), name="ck_periods_status"),
        CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name="ck_periods_closed_at"
        ),
        # Overlapping periods are refused by an EXCLUDE constraint in the migration.
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PeriodStatus.OPEN.value)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalEntry(TimestampMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint(_in_values("status", [s.value for s in EntryStatus]), name="ck_journal_entries_status"),
        CheckConstraint(
            "status = 'draft' OR (posting_date IS NOT NULL AND entry_number IS NOT NULL)",
            name="ck_journal_entries_posted_fields",
        ),
        Index("ix_journal_entries_transaction_date", "transaction_date"),
        Index("ix_journal_entries_status", "status"),
        Index("ix_journal_entries_status_posting_date", "status", "posting_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_number: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=EntryStatus.DRAFT.value)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("owners.id", ondelete="RESTRICT"), nullable=True
    )

    lines: Mapped[list[JournalLine]] = relationship(
        back_populates="entry", order_by="JournalLine.line_number", passive_deletes=False
    )


class JournalLine(TimestampMixin, Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0", name="ck_journal_lines_debit_nonnegative"),
        CheckConstraint("credit >= 0", name="ck_journal_lines_credit_nonnegative"),
        CheckConstraint("(debit > 0) <> (credit > 0)", name="ck_journal_lines_one_side"),
        CheckConstraint("line_number > 0", name="ck_journal_lines_line_number"),
        CheckConstraint("tax_rate IS NULL OR tax_rate >= 0", name="ck_journal_lines_tax_rate"),
        CheckConstraint("tax_amount IS NULL OR tax_amount >= 0", name="ck_journal_lines_tax_amount"),
        Index("ix_journal_lines_account", "account_id"),
        Index("ix_journal_lines_entry", "journal_entry_id"),
        Index("uq_journal_lines_entry_line", "journal_entry_id", "line_number", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    debit: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    credit: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Tax metadata only. The engine stores what a future GST module supplies; it computes no tax.
    tax_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tax_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")

