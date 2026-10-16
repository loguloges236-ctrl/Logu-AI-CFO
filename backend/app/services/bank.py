"""READ-ONLY bank service.

This module only calls the provider's read methods (list_accounts, list_transactions). It has no code path for
transfers, payments, beneficiaries, bank settings or credentials, and a unit test checks the provider interface has
no write-like method. Imported rows are append-only (database trigger). Reconciliation writes suggestions only; it
never changes journal entries. The owner confirms or rejects a suggestion, and only that decision changes.

Provider: any BankDataProvider. With no real provider connected, UnavailableBankProvider refuses every call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.bank.providers import BankDataProvider, ProviderAccount
from app.bank.reconciliation import BankLine, LedgerLine, reconcile
from app.finance.domain import FinanceRuleViolation
from app.models.accounting import JournalEntry, JournalLine
from app.models.finance import BankAccount, BankTransaction, ReconciliationSuggestion
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, unit_of_work

MAX_WINDOW_DAYS = 400


def _rule(code: str, message: str) -> FinanceRuleViolation:
    return FinanceRuleViolation(code, message)


def import_accounts(db: DBSession, request: Request, auth: AuthenticatedSession, provider: BankDataProvider) -> list[dict[str, Any]]:
    """Creates or refreshes read-only account mirrors. Only balances and names change; nothing is moved."""
    with unit_of_work(db):
        provided: list[ProviderAccount] = provider.list_accounts()
        out: list[dict[str, Any]] = []
        for item in provided:
            row = db.scalar(select(BankAccount).where(
                BankAccount.provider == provider.name, BankAccount.external_ref == item.external_ref))
            if row is None:
                row = BankAccount(provider=provider.name, external_ref=item.external_ref,
                                  display_name=item.display_name[:120], currency=item.currency[:3].upper())
                db.add(row)
                action = "bank.account_imported"
            else:
                action = "bank.account_refreshed"
            row.display_name = item.display_name[:120]
            row.provider_balance = item.balance
            row.balance_as_of = item.balance_as_of
            db.flush()
            audit(db, request, auth, action, "bank_account", str(row.id), {"provider": provider.name})
            out.append({"id": str(row.id), "display_name": row.display_name, "balance": str(item.balance),
                        "as_of": item.balance_as_of.isoformat()})
    return out


def link_ledger_account(db: DBSession, request: Request, auth: AuthenticatedSession, bank_account_id: UUID,
                        ledger_account_id: UUID) -> BankAccount:
    """Sets which ledger account mirrors this bank account. Used by reconciliation; it does not post anything."""
    from app.models.accounting import Account

    with unit_of_work(db):
        row = db.get(BankAccount, bank_account_id)
        if row is None:
            raise _rule("not_found", "Bank account was not found")
        account = db.get(Account, ledger_account_id)
        if account is None or account.subtype not in ("bank", "cash", "other_financial"):
            raise _rule("invalid_ledger_account", "Link to a bank or cash ledger account")
        before = row.ledger_account_id
        row.ledger_account_id = ledger_account_id
        db.flush()
        audit(db, request, auth, "bank.ledger_linked", "bank_account", str(row.id),
              {"before": str(before) if before else None, "after": str(ledger_account_id)})
    return row


def _import_key(provider: str, item_id: str, txn_date: date, amount: Decimal) -> str:
    return hashlib.sha256(f"{provider}|{item_id}|{txn_date.isoformat()}|{amount}".encode()).hexdigest()


def import_transactions(db: DBSession, request: Request, auth: AuthenticatedSession, provider: BankDataProvider,
                        bank_account_id: UUID, *, since: date, until: date) -> dict[str, int]:
    """Imports rows in [since, until]. Rows already stored for this account (same external_id) are skipped."""
    if until < since or (until - since).days > MAX_WINDOW_DAYS:
        raise _rule("invalid_window", f"Choose a date range of at most {MAX_WINDOW_DAYS} days")
    with unit_of_work(db):
        account = db.get(BankAccount, bank_account_id)
        if account is None:
            raise _rule("not_found", "Bank account was not found")
        fetched = provider.list_transactions(account.external_ref, since, until)
        known = set(db.scalars(select(BankTransaction.external_id).where(BankTransaction.bank_account_id == account.id)))
        inserted = duplicates = invalid = 0
        for item in fetched:
            if item.external_id in known:
                duplicates += 1
                continue
            if item.amount == 0:
                invalid += 1
                continue
            db.add(BankTransaction(
                bank_account_id=account.id, external_id=item.external_id[:120], txn_date=item.txn_date,
                amount=item.amount, description=(item.description or "")[:255], balance_after=item.balance_after,
                import_key=_import_key(provider.name, item.external_id, item.txn_date, item.amount),
            ))
            known.add(item.external_id)
            inserted += 1
        db.flush()
        audit(db, request, auth, "bank.transactions_imported", "bank_account", str(account.id), {
            "fetched": len(fetched), "inserted": inserted, "duplicates": duplicates, "invalid": invalid,
            "since": since.isoformat(), "until": until.isoformat()})
    return {"fetched": len(fetched), "inserted": inserted, "duplicates": duplicates, "invalid": invalid}


def list_transactions(db: DBSession, bank_account_id: UUID, *, since: date, until: date) -> list[dict[str, Any]]:
    rows = db.scalars(select(BankTransaction).where(
        BankTransaction.bank_account_id == bank_account_id,
        BankTransaction.txn_date >= since, BankTransaction.txn_date <= until,
    ).order_by(BankTransaction.txn_date, BankTransaction.external_id).limit(500))
    return [{"id": str(r.id), "date": r.txn_date.isoformat(), "amount": str(r.amount),
             "direction": "credit" if r.amount > 0 else "debit", "description": r.description,
             "balance_after": None if r.balance_after is None else str(r.balance_after)} for r in rows]


def _ledger_lines(db: DBSession, ledger_account_id: UUID, since: date, until: date) -> tuple[list[LedgerLine], dict[str, UUID]]:
    rows = db.execute(
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalLine.account_id == ledger_account_id, JournalEntry.status == "posted",
               JournalEntry.transaction_date >= since, JournalEntry.transaction_date <= until)
    ).all()
    lines: list[LedgerLine] = []
    entry_of: dict[str, UUID] = {}
    for line, entry in rows:
        lines.append(LedgerLine(str(line.id), entry.transaction_date, line.debit - line.credit,
                                f"{entry.description} {line.description or ''}".strip()))
        entry_of[str(line.id)] = entry.id
    return lines, entry_of


def reconcile_account(db: DBSession, request: Request, auth: AuthenticatedSession, bank_account_id: UUID, *,
                      since: date, until: date) -> dict[str, Any]:
    if until < since or (until - since).days > MAX_WINDOW_DAYS:
        raise _rule("invalid_window", f"Choose a date range of at most {MAX_WINDOW_DAYS} days")
    with unit_of_work(db):
        account = db.get(BankAccount, bank_account_id)
        if account is None:
            raise _rule("not_found", "Bank account was not found")
        if account.ledger_account_id is None:
            raise _rule("no_ledger_account", "Link this bank account to its ledger account first")
        bank_rows = list(db.scalars(select(BankTransaction).where(
            BankTransaction.bank_account_id == account.id,
            BankTransaction.txn_date >= since, BankTransaction.txn_date <= until)))
        ledger, entry_of = _ledger_lines(db, account.ledger_account_id, since, until)
        result = reconcile(
            [BankLine(str(r.id), r.txn_date, r.amount, r.description) for r in bank_rows],
            ledger,
        )
        bank_ids = {str(r.id): r.id for r in bank_rows}
        stored = 0
        for suggestion in result.matched:
            stored += _store(db, bank_ids[suggestion.bank_id], "matched", suggestion.score,
                             entry_of[suggestion.ledger_id])
        for suggestion in result.ambiguous:
            stored += _store(db, bank_ids[suggestion.bank_id], "ambiguous", suggestion.score,
                             entry_of[suggestion.ledger_id])
        for bank_id in result.possible_duplicates:
            stored += _store(db, bank_ids[bank_id], "duplicate", 100, None)
        for bank_id in result.missing_in_ledger:
            stored += _store(db, bank_ids[bank_id], "missing_in_ledger", 0, None)
        for bank_id, reason, ledger_id in result.near_misses:
            stored += _store(db, bank_ids[bank_id], reason, 0, entry_of[ledger_id])
        matched_bank = len(result.matched)
        summary = {
            "bank_transactions": len(bank_rows),
            "ledger_lines": len(ledger),
            "matched": matched_bank,
            "ambiguous": len(result.ambiguous),
            "possible_duplicates": len(result.possible_duplicates),
            "unmatched_bank": len(result.missing_in_ledger),
            "unmatched_ledger": len(result.unmatched_ledger),
            "near_misses": [{"bank_transaction": b, "reason": r} for b, r, _ in result.near_misses],
            "suggestions_stored": stored,
            "note": "Suggestions only. Accounting records were not changed.",
        }
        audit(db, request, auth, "reconciliation.run", "bank_account", str(account.id), {
            "since": since.isoformat(), "until": until.isoformat(), "matched": matched_bank,
            "stored": stored})
    return summary


def _store(db: DBSession, bank_txn_id: UUID, kind: str, score: int, entry_id: UUID | None) -> int:
    """Stores one suggestion unless the same suggestion already exists (re-running reconciliation is idempotent)."""
    exists = db.scalar(select(ReconciliationSuggestion.id).where(
        ReconciliationSuggestion.bank_transaction_id == bank_txn_id,
        ReconciliationSuggestion.kind == kind,
        ReconciliationSuggestion.journal_entry_id.is_(None) if entry_id is None
        else ReconciliationSuggestion.journal_entry_id == entry_id,
        ReconciliationSuggestion.status == "suggested",
    ).limit(1))
    if exists is not None:
        return 0
    db.add(ReconciliationSuggestion(bank_transaction_id=bank_txn_id, journal_entry_id=entry_id, kind=kind, score=score))
    db.flush()
    return 1


def list_suggestions(db: DBSession, *, status: str = "suggested", limit: int = 100) -> list[dict[str, Any]]:
    if status not in ("suggested", "confirmed", "rejected"):
        raise _rule("invalid_status", "Status must be suggested, confirmed or rejected")
    rows = db.scalars(select(ReconciliationSuggestion).where(ReconciliationSuggestion.status == status)
                      .order_by(ReconciliationSuggestion.created_at).limit(min(limit, 200)))
    return [{"id": str(r.id), "bank_transaction_id": str(r.bank_transaction_id),
             "journal_entry_id": str(r.journal_entry_id) if r.journal_entry_id else None,
             "kind": r.kind, "score": r.score, "status": r.status} for r in rows]


def decide_suggestion(db: DBSession, request: Request, auth: AuthenticatedSession, suggestion_id: UUID, decision: str) -> ReconciliationSuggestion:
    from datetime import datetime, timezone

    if decision not in ("confirmed", "rejected"):
        raise _rule("invalid_decision", "Decision must be confirmed or rejected")
    with unit_of_work(db):
        row = db.scalar(select(ReconciliationSuggestion).where(ReconciliationSuggestion.id == suggestion_id).with_for_update())
        if row is None:
            raise _rule("not_found", "Suggestion was not found")
        if row.status != "suggested":
            raise _rule("already_decided", "This suggestion already has a decision")
        if decision == "confirmed":
            if row.kind in ("duplicate", "missing_in_ledger"):
                raise _rule("not_confirmable", "This suggestion can only be rejected or resolved by a journal entry")
            clash = db.scalar(select(ReconciliationSuggestion.id).where(
                ReconciliationSuggestion.bank_transaction_id == row.bank_transaction_id,
                ReconciliationSuggestion.status == "confirmed").limit(1))
            if clash is not None:
                raise _rule("already_confirmed", "This bank transaction already has a confirmed match")
        row.status = decision
        row.decided_at = datetime.now(timezone.utc)
        db.flush()
        audit(db, request, auth, f"reconciliation.{decision}", "reconciliation_suggestion", str(row.id), {
            "kind": row.kind, "bank_transaction": str(row.bank_transaction_id)})
    return row
