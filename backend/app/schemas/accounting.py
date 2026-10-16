"""Request and response models for the accounting API.

Money is always a string on the wire (for example "1250.50"). Floats are not accepted for input,
and output never serialises Decimal as a JSON number, so no binary floating point enters the flow.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

CategoryLiteral = Literal["asset", "liability", "equity", "income", "expense"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountCreate(_Strict):
    code: str = Field(min_length=4, max_length=10, pattern=r"^[0-9]{4,10}$")
    name: str = Field(min_length=1, max_length=120)
    category: CategoryLiteral
    subtype: str = Field(min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=500)
    parent_id: UUID | None = None


class AccountUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    subtype: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: StrictBool | None = None


class AccountOut(BaseModel):
    id: UUID
    code: str
    name: str
    category: str
    subtype: str
    description: str | None
    parent_id: UUID | None
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class SetupOut(BaseModel):
    created: int


class LineIn(_Strict):
    account_id: UUID
    debit: str = Field(default="0", max_length=24)
    credit: str = Field(default="0", max_length=24)
    description: str | None = Field(default=None, max_length=255)
    tax_name: str | None = Field(default=None, max_length=60)
    tax_type: str | None = Field(default=None, max_length=30)
    tax_rate: str | None = Field(default=None, max_length=12, pattern=r"^[0-9]{1,3}(\.[0-9]{1,4})?$")
    tax_amount: str | None = Field(default=None, max_length=24)


class EntryIn(_Strict):
    transaction_date: date
    description: str = Field(min_length=1, max_length=500)
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: str | None = Field(default=None, max_length=120)
    lines: list[LineIn] = Field(default_factory=list, max_length=200)


class PostIn(_Strict):
    posting_date: date | None = None


class ReverseIn(_Strict):
    posting_date: date | None = None
    description: str | None = Field(default=None, max_length=500)


class LineOut(BaseModel):
    id: UUID
    line_number: int
    account_id: UUID
    account_code: str
    debit: str
    credit: str
    description: str | None
    tax_name: str | None
    tax_type: str | None
    tax_rate: str | None
    tax_amount: str | None


class EntryOut(BaseModel):
    id: UUID
    entry_number: int | None
    transaction_date: date
    posting_date: date | None
    reference_type: str | None
    reference_id: str | None
    description: str
    status: str
    reverses_entry_id: UUID | None
    created_at: datetime
    updated_at: datetime
    lines: list[LineOut]


class EntryListOut(BaseModel):
    items: list[EntryOut]
    total: int
    limit: int
    offset: int


class PeriodIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    start_date: date
    end_date: date
    status: str
    closed_at: datetime | None


class LedgerRowOut(BaseModel):
    posting_date: date
    entry_number: int
    description: str
    debit: str
    credit: str
    balance: str


class LedgerOut(BaseModel):
    account_id: UUID
    account_code: str
    account_name: str
    category: str
    opening_balance: str
    debit_total: str
    credit_total: str
    closing_balance: str
    rows: list[LedgerRowOut]


class TrialBalanceRowOut(BaseModel):
    code: str
    name: str
    category: str
    debit_balance: str
    credit_balance: str


class TrialBalanceOut(BaseModel):
    rows: list[TrialBalanceRowOut]
    total_debit: str
    total_credit: str
    is_balanced: bool


class ProfitAndLossOut(BaseModel):
    income: str
    expenses: str
    net_profit: str


class BalanceSheetOut(BaseModel):
    assets: str
    liabilities: str
    equity: str
    current_earnings: str
    liabilities_and_equity: str
    is_balanced: bool


class CashPositionItemOut(BaseModel):
    code: str
    name: str
    balance: str


class CashPositionOut(BaseModel):
    items: list[CashPositionItemOut]
    total: str


class DashboardOut(BaseModel):
    draft_entries: int
    posted_entries: int
    open_period: str | None
    cash_total: str
    cash_accounts: int
    trial_balance_balanced: bool
    balance_sheet_balanced: bool
