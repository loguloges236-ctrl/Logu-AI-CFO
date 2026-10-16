"""Pure double-entry accounting rules.

No framework, database or clock access happens in this module, so every rule can be unit-tested
with the standard library and `decimal`. Services and the database enforce the same rules again;
this module is the single source of truth for what "balanced", "valid" and "open" mean.

Money is `decimal.Decimal` with scale 2 (INR paise). Floats are rejected at the boundary, and
amounts with more than two decimal places are rejected rather than rounded silently.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MONEY_SCALE = Decimal("0.01")
MAX_MONEY = Decimal("9999999999999999.99")  # NUMERIC(18,2)
MIN_LINES_PER_ENTRY = 2
MAX_LINES_PER_ENTRY = 200
ZERO = Decimal("0.00")

_MONEY_PATTERN = re.compile(r"^-?\d{1,16}(\.\d{1,2})?$")
_CODE_PATTERN = re.compile(r"^[0-9]{4,10}$")


class AccountCategory(enum.StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class Side(enum.StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class PeriodStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class EntryStatus(enum.StrEnum):
    DRAFT = "draft"
    POSTED = "posted"


# Normal balance: the side that increases the account. Assets and expenses grow on debit;
# liabilities, equity and income grow on credit.
NORMAL_SIDE: dict[AccountCategory, Side] = {
    AccountCategory.ASSET: Side.DEBIT,
    AccountCategory.EXPENSE: Side.DEBIT,
    AccountCategory.LIABILITY: Side.CREDIT,
    AccountCategory.EQUITY: Side.CREDIT,
    AccountCategory.INCOME: Side.CREDIT,
}

# Subtypes are a closed, documented list so reports and cash-position queries are deterministic.
ACCOUNT_SUBTYPES: frozenset[str] = frozenset(
    {
        "header",
        "cash",
        "petty_cash",
        "bank",
        "other_financial",
        "receivable",
        "inventory",
        "payable",
        "loan",
        "capital",
        "sales",
        "operating_expense",
        "general",
    }
)
CASH_AND_BANK_SUBTYPES: frozenset[str] = frozenset({"cash", "petty_cash", "bank", "other_financial"})


class AccountingRuleViolation(ValueError):
    """A business or integrity rule failed. `code` is stable and safe to show to clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- money


def parse_money(value: object, *, field: str = "amount") -> Decimal:
    """Parse a monetary value from a string or int. Floats and booleans are refused."""
    if isinstance(value, bool) or isinstance(value, float):
        raise AccountingRuleViolation("money_type", f"{field} must be a decimal string, not a number")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise AccountingRuleViolation("money_type", f"{field} must be a decimal string")
    if not _MONEY_PATTERN.fullmatch(text):
        raise AccountingRuleViolation(
            "money_format", f"{field} must have at most 16 digits and at most 2 decimal places"
        )
    amount = Decimal(text).quantize(MONEY_SCALE)
    if amount == 0:
        return ZERO  # normalises "-0.00"
    return amount


def money_text(amount: Decimal) -> str:
    """Canonical string form (always two decimals). Used in every API response."""
    return str(amount.quantize(MONEY_SCALE))


# ---------------------------------------------------------------- accounts


def validate_account_code(code: str) -> str:
    cleaned = code.strip()
    if not _CODE_PATTERN.fullmatch(cleaned):
        raise AccountingRuleViolation("account_code_format", "Account code must be 4 to 10 digits")
    return cleaned


def validate_subtype(subtype: str) -> str:
    if subtype not in ACCOUNT_SUBTYPES:
        raise AccountingRuleViolation("account_subtype", "Unknown account subtype")
    return subtype


def validate_parent_category(child: AccountCategory, parent: AccountCategory) -> None:
    """A child account must sit in the same category as its parent (hierarchy stays coherent)."""
    if child != parent:
        raise AccountingRuleViolation(
            "parent_category_mismatch", "A child account must have the same category as its parent"
        )


def normal_side(category: AccountCategory) -> Side:
    return NORMAL_SIDE[category]


# ---------------------------------------------------------------- journal entries


@dataclass(frozen=True)
class LineAmounts:
    """One journal line in its validated, decimal form."""

    account_id: str
    debit: Decimal
    credit: Decimal


def validate_line(debit: Decimal, credit: Decimal) -> None:
    if debit < 0 or credit < 0:
        raise AccountingRuleViolation("negative_amount", "Debit and credit must not be negative")
    if debit > MAX_MONEY or credit > MAX_MONEY:
        raise AccountingRuleViolation("amount_too_large", "Amount exceeds the supported maximum")
    if (debit > 0) == (credit > 0):
        # Both zero, or both positive: a line must carry exactly one side.
        raise AccountingRuleViolation(
            "line_side", "Each line must have either a debit or a credit amount, and that amount must be above zero"
        )


def validate_entry(lines: Sequence[LineAmounts]) -> tuple[Decimal, Decimal]:
    """Validate a journal entry and return (total_debit, total_credit). Raises on any rule failure."""
    if len(lines) < MIN_LINES_PER_ENTRY:
        raise AccountingRuleViolation("too_few_lines", "A journal entry needs at least two lines")
    if len(lines) > MAX_LINES_PER_ENTRY:
        raise AccountingRuleViolation("too_many_lines", "A journal entry has too many lines")
    total_debit = ZERO
    total_credit = ZERO
    for line in lines:
        validate_line(line.debit, line.credit)
        total_debit += line.debit
        total_credit += line.credit
    if total_debit != total_credit:
        raise AccountingRuleViolation(
            "unbalanced_entry",
            f"Total debit {money_text(total_debit)} does not equal total credit {money_text(total_credit)}",
        )
    return total_debit, total_credit


def reverse_lines(lines: Sequence[LineAmounts]) -> list[LineAmounts]:
    """Mirror every line: debits become credits and vice versa. Posting this nets the original to zero."""
    return [LineAmounts(line.account_id, line.credit, line.debit) for line in lines]


def validate_period(start: date, end: date) -> None:
    if start > end:
        raise AccountingRuleViolation("invalid_period", "Period start must be on or before its end date")


def period_contains(start: date, end: date, on: date) -> bool:
    return start <= on <= end


# ---------------------------------------------------------------- balances and reports


@dataclass(frozen=True)
class AccountTotals:
    code: str
    name: str
    category: AccountCategory
    debit_total: Decimal
    credit_total: Decimal


def natural_balance(category: AccountCategory, debit_total: Decimal, credit_total: Decimal) -> Decimal:
    """Balance on the account's normal side. Negative means the account is on the abnormal side."""
    net = debit_total - credit_total
    return net if normal_side(category) is Side.DEBIT else -net


@dataclass(frozen=True)
class TrialBalanceRow:
    code: str
    name: str
    category: AccountCategory
    debit_balance: Decimal
    credit_balance: Decimal


@dataclass(frozen=True)
class TrialBalance:
    rows: tuple[TrialBalanceRow, ...]
    total_debit: Decimal
    total_credit: Decimal

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


def trial_balance(totals: Iterable[AccountTotals]) -> TrialBalance:
    rows: list[TrialBalanceRow] = []
    total_debit = ZERO
    total_credit = ZERO
    for item in totals:
        net = item.debit_total - item.credit_total
        if net == 0:
            debit_balance = credit_balance = ZERO
        elif net > 0:
            debit_balance, credit_balance = net, ZERO
        else:
            debit_balance, credit_balance = ZERO, -net
        if debit_balance == 0 and credit_balance == 0 and item.debit_total == 0 and item.credit_total == 0:
            continue  # never-used accounts are omitted
        rows.append(TrialBalanceRow(item.code, item.name, item.category, debit_balance, credit_balance))
        total_debit += debit_balance
        total_credit += credit_balance
    rows.sort(key=lambda row: row.code)
    return TrialBalance(tuple(rows), total_debit, total_credit)


@dataclass(frozen=True)
class ProfitAndLoss:
    income: Decimal
    expenses: Decimal

    @property
    def net_profit(self) -> Decimal:
        return self.income - self.expenses


def profit_and_loss(totals: Iterable[AccountTotals]) -> ProfitAndLoss:
    income = ZERO
    expenses = ZERO
    for item in totals:
        if item.category is AccountCategory.INCOME:
            income += natural_balance(item.category, item.debit_total, item.credit_total)
        elif item.category is AccountCategory.EXPENSE:
            expenses += natural_balance(item.category, item.debit_total, item.credit_total)
    return ProfitAndLoss(income, expenses)


@dataclass(frozen=True)
class BalanceSheet:
    assets: Decimal
    liabilities: Decimal
    equity: Decimal
    current_earnings: Decimal

    @property
    def liabilities_and_equity(self) -> Decimal:
        return self.liabilities + self.equity + self.current_earnings

    @property
    def is_balanced(self) -> bool:
        return self.assets == self.liabilities_and_equity


def balance_sheet(totals: Iterable[AccountTotals]) -> BalanceSheet:
    assets = liabilities = equity = income = expenses = ZERO
    for item in totals:
        value = natural_balance(item.category, item.debit_total, item.credit_total)
        if item.category is AccountCategory.ASSET:
            assets += value
        elif item.category is AccountCategory.LIABILITY:
            liabilities += value
        elif item.category is AccountCategory.EQUITY:
            equity += value
        elif item.category is AccountCategory.INCOME:
            income += value
        else:
            expenses += value
    return BalanceSheet(assets, liabilities, equity, income - expenses)


@dataclass(frozen=True)
class LedgerLine:
    posting_date: date
    entry_number: int
    description: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


def ledger_with_running_balance(
    category: AccountCategory,
    movements: Sequence[tuple[date, int, str, Decimal, Decimal]],
    opening_balance: Decimal = ZERO,
) -> list[LedgerLine]:
    """Build ledger rows in the given order. Each row carries the running natural balance."""
    running = opening_balance
    rows: list[LedgerLine] = []
    for posting_date, entry_number, description, debit, credit in movements:
        running += natural_balance(category, debit, credit)
        rows.append(LedgerLine(posting_date, entry_number, description, debit, credit, running))
    return rows
