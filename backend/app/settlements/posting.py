"""Journal lines for a settlement. Pure and deterministic; the caller posts them through the Phase 2 accounting service.

Receivable receipt (customer pays):  Dr Cash/Bank,  Cr Accounts Receivable
Payable payment (we pay supplier):   Dr Accounts Payable,  Cr Cash/Bank
A reversal is a negative settlement. It produces the same lines with the sides swapped, so the original journal is never
edited and the reversal is its own balanced entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.finance.domain import FinanceRuleViolation

CENT = Decimal("0.01")
DIRECTIONS = ("receivable", "payable")


@dataclass(frozen=True)
class PostingLine:
    account_id: UUID
    debit: Decimal
    credit: Decimal


def settlement_journal(
    direction: str,
    amount: Decimal,
    cash_account_id: UUID,
    control_account_id: UUID,
) -> list[PostingLine]:
    if direction not in DIRECTIONS:
        raise FinanceRuleViolation("invalid_direction", "Direction must be receivable or payable")
    if amount == 0:
        raise FinanceRuleViolation("zero_settlement", "A settlement cannot be zero")
    if cash_account_id == control_account_id:
        raise FinanceRuleViolation("same_account", "Cash and control accounts must differ")
    value = abs(amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if value != abs(amount):
        raise FinanceRuleViolation("precision", "Settlement amounts have at most two decimal places")

    # Positive receipt/payment: money in (receivable) or out (payable). A negative amount is a reversal of that flow.
    cash_in = direction == "receivable"
    if amount < 0:
        cash_in = not cash_in
    if cash_in:
        lines = [PostingLine(cash_account_id, value, Decimal("0.00")), PostingLine(control_account_id, Decimal("0.00"), value)]
    else:
        lines = [PostingLine(control_account_id, value, Decimal("0.00")), PostingLine(cash_account_id, Decimal("0.00"), value)]
    assert_balanced(lines)
    return lines


def assert_balanced(lines: list[PostingLine]) -> None:
    debit = sum((line.debit for line in lines), Decimal("0.00"))
    credit = sum((line.credit for line in lines), Decimal("0.00"))
    if debit != credit or debit <= 0:
        raise FinanceRuleViolation("unbalanced", f"Journal is not balanced (debit {debit}, credit {credit})")


def posting_key(settlement_id: UUID) -> str:
    """Idempotency key for one settlement row. Used by the service before posting, and by a unique index."""
    return f"settlement:{settlement_id}"
