"""Receivable and payable status. Pure arithmetic on Decimal values taken from the database.

Outstanding = document grand_total - net settled. The database trigger already refuses any settlement that would
push the net outside 0..grand_total; these checks repeat the rule so a bad value cannot reach a report.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.finance.domain import FinanceRuleViolation

PAYMENT_STATUSES = ("unpaid", "partially_paid", "paid")


@dataclass(frozen=True)
class SettlementStatus:
    total: Decimal
    settled: Decimal
    outstanding: Decimal
    status: str


def settlement_status(total: Decimal, settled: Decimal) -> SettlementStatus:
    if total < 0:
        raise FinanceRuleViolation("negative_total", "A document total cannot be negative")
    if settled < 0:
        raise FinanceRuleViolation("negative_settlement", "Net settlement cannot be negative")
    if settled > total:
        raise FinanceRuleViolation("overpaid", f"Settlements ({settled}) exceed the document total ({total})")
    outstanding = total - settled
    if settled == 0:
        status = "unpaid"
    elif outstanding == 0:
        status = "paid"
    else:
        status = "partially_paid"
    return SettlementStatus(total, settled, outstanding, status)
