"""Deterministic reconciliation of bank transactions against ledger lines.

Output is advice only. The caller stores suggestions and the owner confirms them; accounting records are never
changed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

DATE_TOLERANCE_DAYS = 3

# Stored suggestion kinds. Must equal the migration literal list (tests/unit/test_finance_static.py).
SUGGESTION_KINDS = ("matched", "ambiguous", "duplicate", "missing_in_ledger", "date_mismatch", "amount_mismatch")


@dataclass(frozen=True)
class BankLine:
    id: str
    txn_date: date
    amount: Decimal  # signed, same convention as the provider
    description: str


@dataclass(frozen=True)
class LedgerLine:
    id: str
    txn_date: date
    amount: Decimal  # signed: positive = increases the bank ledger account
    description: str


@dataclass(frozen=True)
class Suggestion:
    bank_id: str
    ledger_id: str
    score: int
    status: str  # "matched" | "ambiguous"


@dataclass
class ReconciliationResult:
    matched: list[Suggestion] = field(default_factory=list)
    ambiguous: list[Suggestion] = field(default_factory=list)
    possible_duplicates: list[str] = field(default_factory=list)
    missing_in_ledger: list[str] = field(default_factory=list)
    unmatched_ledger: list[str] = field(default_factory=list)
    # (bank_line_id, reason, ledger_line_id): an unmatched bank line that has a close but imperfect counterpart.
    near_misses: list[tuple[str, str, str]] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def description_score(a: str, b: str) -> int:
    """0-100 token overlap. Deterministic; no fuzzy model."""
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0
    return round(100 * len(left & right) / len(left | right))


NEAR_MISS_DAYS = 30
AMOUNT_MISMATCH_SCORE = 50


def _near_miss(line: BankLine, ledger: list[LedgerLine], used: set[str]) -> tuple[str, str] | None:
    """Explain why an unmatched bank line almost matched. Never used to match it."""
    best: tuple[int, str, str] | None = None
    for entry in ledger:
        if entry.id in used:
            continue
        gap = abs((entry.txn_date - line.txn_date).days)
        if entry.amount == line.amount and DATE_TOLERANCE_DAYS < gap <= NEAR_MISS_DAYS:
            candidate = (0, "date_mismatch", entry.id)
        elif entry.amount != line.amount and gap <= DATE_TOLERANCE_DAYS:
            score = description_score(line.description, entry.description)
            if score < AMOUNT_MISMATCH_SCORE:
                continue
            candidate = (1, "amount_mismatch", entry.id)
        else:
            continue
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return None if best is None else (best[1], best[2])


def reconcile(bank: list[BankLine], ledger: list[LedgerLine]) -> ReconciliationResult:
    result = ReconciliationResult()
    ordered = sorted(bank, key=lambda b: (b.txn_date, b.id))

    seen: dict[tuple[date, Decimal, str], str] = {}
    for line in ordered:
        key = (line.txn_date, line.amount, " ".join(sorted(_tokens(line.description))))
        if key in seen:
            result.possible_duplicates.append(line.id)
        else:
            seen[key] = line.id

    used_ledger: set[str] = set()
    for line in ordered:
        if line.id in result.possible_duplicates:
            continue
        candidates = []
        for entry in ledger:
            if entry.id in used_ledger or entry.amount != line.amount:
                continue
            gap = abs((entry.txn_date - line.txn_date).days)
            if gap <= DATE_TOLERANCE_DAYS:
                candidates.append((gap, -description_score(line.description, entry.description), entry.id, entry))
        if not candidates:
            result.missing_in_ledger.append(line.id)
            miss = _near_miss(line, ledger, used_ledger)
            if miss is not None:
                result.near_misses.append((line.id, miss[0], miss[1]))
            continue
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        best = candidates[0]
        tied = [c for c in candidates if (c[0], c[1]) == (best[0], best[1])]
        score = -best[1]
        if len(tied) == 1:
            result.matched.append(Suggestion(line.id, best[2], score, "matched"))
            used_ledger.add(best[2])
        else:
            result.ambiguous.append(Suggestion(line.id, best[2], score, "ambiguous"))

    matched_ids = {s.ledger_id for s in result.matched}
    result.unmatched_ledger = sorted(e.id for e in ledger if e.id not in matched_ids)
    return result
