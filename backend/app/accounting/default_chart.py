"""Generic default Chart of Accounts seeded when the accounting module is first set up.

These are a neutral template, not the owner's real business accounts. The owner can rename, add,
or deactivate accounts afterwards. System-flagged accounts keep their code and category.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.accounting.domain import AccountCategory


@dataclass(frozen=True)
class DefaultAccount:
    code: str
    name: str
    category: AccountCategory
    subtype: str
    parent_code: str | None
    description: str = ""


DEFAULT_CHART: tuple[DefaultAccount, ...] = (
    DefaultAccount("1000", "Assets", AccountCategory.ASSET, "header", None),
    DefaultAccount("1100", "Cash", AccountCategory.ASSET, "cash", "1000", "Cash on hand"),
    DefaultAccount("1110", "Petty cash", AccountCategory.ASSET, "petty_cash", "1000", "Small cash float"),
    DefaultAccount("1200", "Bank", AccountCategory.ASSET, "bank", "1000", "Bank accounts (ledger only; no bank connection)"),
    DefaultAccount("1300", "Accounts receivable", AccountCategory.ASSET, "receivable", "1000"),
    DefaultAccount("1400", "Inventory", AccountCategory.ASSET, "inventory", "1000"),
    DefaultAccount("2000", "Liabilities", AccountCategory.LIABILITY, "header", None),
    DefaultAccount("2100", "Accounts payable", AccountCategory.LIABILITY, "payable", "2000"),
    DefaultAccount("2200", "Loans", AccountCategory.LIABILITY, "loan", "2000"),
    DefaultAccount("3000", "Equity", AccountCategory.EQUITY, "capital", None),
    DefaultAccount("4000", "Income", AccountCategory.INCOME, "header", None),
    DefaultAccount("4100", "Sales", AccountCategory.INCOME, "sales", "4000"),
    DefaultAccount("5000", "Expenses", AccountCategory.EXPENSE, "header", None),
    DefaultAccount("5100", "Rent", AccountCategory.EXPENSE, "operating_expense", "5000"),
    DefaultAccount("5200", "Salary", AccountCategory.EXPENSE, "operating_expense", "5000"),
    DefaultAccount("5300", "Utilities", AccountCategory.EXPENSE, "operating_expense", "5000"),
)
