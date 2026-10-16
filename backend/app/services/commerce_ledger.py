"""Controlled accounting integration for Phase 3 business events.

Business services never write journal lines themselves. They describe the economic effect as debit and credit
amounts per ACCOUNT ROLE; this module maps roles to the owner's configured accounts and calls the Phase 2
accounting service with `commit=False`, so the journal entry commits together with the business event.

Roles are configured in commerce_account_mappings. If a required role is missing, posting is refused with the list
of missing roles. Nothing is guessed from account codes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.accounting import domain as ad
from app.accounting.domain import AccountingRuleViolation
from app.commerce import domain as cd
from app.models import Account, CommerceAccountMapping, JournalEntry
from app.services import accounting as ledger
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, rule


@dataclass(frozen=True)
class Posting:
    role: str
    amount: Decimal
    debit: bool  # True = debit, False = credit


def configured_accounts(db: DBSession) -> dict[str, UUID]:
    return {row.role: row.account_id for row in db.scalars(select(CommerceAccountMapping))}


def require_roles(db: DBSession, roles: Iterable[str]) -> dict[str, UUID]:
    configured = configured_accounts(db)
    missing = cd.missing_roles(roles, configured)
    if missing:
        raise rule(
            "accounting_not_configured",
            "Set these accounting roles first: " + ", ".join(missing),
        )
    return configured


def _net_lines(postings: Iterable[tuple[UUID, Decimal, bool]]) -> list[ledger.LineInput]:
    """Net the amounts per account so that each account appears once, on the side of its net balance."""
    net: dict[UUID, Decimal] = {}
    for account_id, amount, is_debit in postings:
        if amount == 0:
            continue
        signed = amount if is_debit else -amount
        net[account_id] = net.get(account_id, Decimal("0")) + signed
    lines: list[ledger.LineInput] = []
    for account_id, value in net.items():
        if value == 0:
            continue
        if value > 0:
            lines.append(ledger.LineInput(account_id=account_id, debit=cd.money(value), credit="0"))
        else:
            lines.append(ledger.LineInput(account_id=account_id, debit="0", credit=cd.money(-value)))
    return lines


def post_entry(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    description: str,
    posting_date: date,
    reference_type: str,
    reference_id: str,
    postings: list[tuple[UUID, Decimal, bool]],
    action: str,
) -> JournalEntry:
    """Create and post one balanced journal entry inside the caller's transaction (no commit here)."""
    lines = _net_lines(postings)
    if len(lines) < ad.MIN_LINES_PER_ENTRY:
        raise rule("nothing_to_post", "This document has no amount to post")
    try:
        draft = ledger.create_draft(
            db,
            request,
            auth,
            ledger.EntryDraftInput(
                transaction_date=posting_date,
                description=description[:500],
                reference_type=reference_type,
                reference_id=reference_id,
                lines=lines,
            ),
            commit=False,
        )
        entry = ledger.post_entry(db, request, auth, draft.id, posting_date=posting_date, commit=False)
    except AccountingRuleViolation as error:
        # Keep the code stable and generic, but let the caller see the accounting reason.
        raise rule(error.code, error.message) from None
    audit(
        db,
        request,
        auth,
        "accounting.integration_posted",
        reference_type,
        reference_id,
        {"journal_entry_number": entry.entry_number, "action": action},
    )
    return entry


def reverse_entry(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    entry_id: UUID,
    *,
    posting_date: date,
    reference_type: str,
    reference_id: str,
) -> JournalEntry:
    try:
        entry = ledger.reverse_entry(db, request, auth, entry_id, posting_date=posting_date, commit=False)
    except AccountingRuleViolation as error:
        raise rule(error.code, error.message) from None
    audit(
        db,
        request,
        auth,
        "accounting.integration_reversed",
        reference_type,
        reference_id,
        {"journal_entry_number": entry.entry_number},
    )
    return entry


def account_is_usable(db: DBSession, account_id: UUID, category: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise rule("invalid_account", "The selected account does not exist")
    if not account.is_active or account.subtype == "header":
        raise rule("invalid_account", "Choose an active detail account")
    if account.category != category:
        raise rule("invalid_account", f"The account must be a {category} account")
    return account
