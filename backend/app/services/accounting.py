"""Core accounting services.

Every mutation: validates with `app.accounting.domain`, writes through SQLAlchemy, commits
explicitly, and records an audit row. Database triggers (migration 20261008_0002) repeat the
critical rules so that a bug here cannot silently break the books.

Reporting uses POSTED entries only and filters by POSTING date (the accounting date).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DBSession

from app.accounting import domain as d
from app.accounting.default_chart import DEFAULT_CHART
from app.core.clock import utcnow
from app.models import Account, AccountingPeriod, JournalEntry, JournalLine
from app.services.audit import write_audit
from app.services.auth import AuthenticatedSession

MAX_PAGE_SIZE = 200


def _rule(code: str, message: str) -> d.AccountingRuleViolation:
    return d.AccountingRuleViolation(code, message)


def _not_found(what: str) -> d.AccountingRuleViolation:
    return d.AccountingRuleViolation("not_found", f"{what} was not found")


def _persist(db: DBSession) -> None:
    """Flush and commit. Database-level rule failures are turned into a safe, generic rule error."""
    try:
        db.commit()
    except DBAPIError:
        db.rollback()
        raise _rule("database_rule", "The database rejected this change. Nothing was recorded.") from None


def _finish(db: DBSession, commit: bool) -> None:
    """Commit, or only flush when the caller owns a larger transaction (Phase 3 business events)."""
    if commit:
        _persist(db)
    else:
        _flush(db)


def _flush(db: DBSession) -> None:
    try:
        db.flush()
    except DBAPIError:
        db.rollback()
        raise _rule("database_rule", "The database rejected this change. Nothing was recorded.") from None


def _category(value: str) -> d.AccountCategory:
    try:
        return d.AccountCategory(value)
    except ValueError:
        raise _rule("account_category", "Unknown account category") from None


def _audit(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    write_audit(
        db,
        request,
        action=action,
        success=True,
        owner_id=auth.owner.id,
        session_id=auth.session.id,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )


# ---------------------------------------------------------------- chart of accounts


def create_default_chart(db: DBSession, request: Request, auth: AuthenticatedSession) -> int:
    if db.scalar(select(func.count()).select_from(Account)):
        raise _rule("already_initialized", "The chart of accounts already exists")
    created: dict[str, Account] = {}
    for item in DEFAULT_CHART:
        parent = created.get(item.parent_code) if item.parent_code else None
        account = Account(
            id=uuid.uuid4(),
            code=item.code,
            name=item.name,
            category=item.category.value,
            subtype=item.subtype,
            description=item.description or None,
            parent_id=parent.id if parent else None,
            is_active=True,
            is_system=True,
        )
        db.add(account)
        _flush(db)  # parents first, so self-referencing parent_id resolves in insert order
        created[item.code] = account
        _audit(db, request, auth, "account.created", "account", item.code, {"system": True})
    _persist(db)
    return len(created)


def list_accounts(db: DBSession, *, include_inactive: bool = True) -> list[Account]:
    stmt = select(Account).order_by(Account.code)
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    return list(db.scalars(stmt))


def get_account(db: DBSession, account_id: UUID) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise _not_found("Account")
    return account


def create_account(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    code: str,
    name: str,
    category: str,
    subtype: str,
    description: str | None,
    parent_id: UUID | None,
) -> Account:
    code = d.validate_account_code(code)
    subtype = d.validate_subtype(subtype)
    account_category = _category(category)
    if db.scalar(select(Account.id).where(Account.code == code).limit(1)) is not None:
        raise _rule("duplicate_code", "An account with this code already exists")

    parent: Account | None = None
    if parent_id is not None:
        parent = db.get(Account, parent_id)
        if parent is None:
            raise _rule("invalid_parent", "The parent account was not found")
        if parent.subtype != "header":
            raise _rule("parent_not_header", "Accounts can only be grouped under a header account")
        d.validate_parent_category(account_category, _category(parent.category))

    account = Account(
        id=uuid.uuid4(),
        code=code,
        name=name.strip(),
        category=account_category.value,
        subtype=subtype,
        description=description or None,
        parent_id=parent.id if parent else None,
        is_active=True,
        is_system=False,
    )
    db.add(account)
    _audit(db, request, auth, "account.created", "account", code, {"subtype": subtype})
    _persist(db)
    return account


def update_account(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    account_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    subtype: str | None = None,
    is_active: bool | None = None,
) -> Account:
    """Code, category and parent are fixed after creation (they affect reported history)."""
    account = get_account(db, account_id)
    changed: dict[str, object] = {}
    if name is not None and name.strip() != account.name:
        account.name = name.strip()
        changed["name"] = True
    if description is not None and (description or None) != account.description:
        account.description = description or None
        changed["description"] = True
    if subtype is not None and d.validate_subtype(subtype) != account.subtype:
        account.subtype = subtype
        changed["subtype"] = subtype
    if changed:
        _audit(db, request, auth, "account.updated", "account", account.code, changed)
    if is_active is not None and is_active != account.is_active:
        account.is_active = is_active
        action = "account.activated" if is_active else "account.deactivated"
        _audit(db, request, auth, action, "account", account.code)
    _persist(db)
    return account


# ---------------------------------------------------------------- periods


def list_periods(db: DBSession) -> list[AccountingPeriod]:
    return list(db.scalars(select(AccountingPeriod).order_by(AccountingPeriod.start_date)))


def create_period(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    name: str,
    start_date: date,
    end_date: date,
) -> AccountingPeriod:
    d.validate_period(start_date, end_date)
    if db.scalar(select(AccountingPeriod.id).where(AccountingPeriod.name == name.strip()).limit(1)):
        raise _rule("duplicate_period_name", "A period with this name already exists")
    overlap = db.scalar(
        select(AccountingPeriod.id)
        .where(AccountingPeriod.start_date <= end_date, AccountingPeriod.end_date >= start_date)
        .limit(1)
    )
    if overlap is not None:
        raise _rule("period_overlap", "This period overlaps an existing accounting period")
    period = AccountingPeriod(
        id=uuid.uuid4(),
        name=name.strip(),
        start_date=start_date,
        end_date=end_date,
        status=d.PeriodStatus.OPEN.value,
        closed_at=None,
    )
    db.add(period)
    _audit(db, request, auth, "period.created", "accounting_period", str(period.id), {"name": period.name})
    _persist(db)
    return period


def close_period(db: DBSession, request: Request, auth: AuthenticatedSession, period_id: UUID) -> AccountingPeriod:
    period = db.scalar(select(AccountingPeriod).where(AccountingPeriod.id == period_id).with_for_update())
    if period is None:
        raise _not_found("Accounting period")
    if period.status == d.PeriodStatus.CLOSED.value:
        raise _rule("already_closed", "This accounting period is already closed")
    period.status = d.PeriodStatus.CLOSED.value
    period.closed_at = utcnow()
    _audit(db, request, auth, "period.closed", "accounting_period", str(period.id), {"name": period.name})
    _persist(db)
    return period


def _open_period_covering(db: DBSession, on: date) -> AccountingPeriod | None:
    """Return the OPEN period covering `on`, holding a shared row lock until commit.

    close_period takes the exclusive lock on the same row, so a period cannot be closed between
    this check and the posting commit (no check-then-act race).
    """
    return db.scalar(
        select(AccountingPeriod)
        .where(
            AccountingPeriod.status == d.PeriodStatus.OPEN.value,
            AccountingPeriod.start_date <= on,
            AccountingPeriod.end_date >= on,
        )
        .limit(1)
        .with_for_update(read=True)
    )


# ---------------------------------------------------------------- journal entries


@dataclass(frozen=True)
class LineInput:
    account_id: UUID
    debit: str
    credit: str
    description: str | None = None
    tax_name: str | None = None
    tax_type: str | None = None
    tax_rate: str | None = None
    tax_amount: str | None = None


@dataclass
class _PreparedLine:
    account: Account
    debit: Decimal
    credit: Decimal
    source: LineInput


@dataclass(frozen=True)
class EntryDraftInput:
    transaction_date: date
    description: str
    reference_type: str | None
    reference_id: str | None
    lines: list[LineInput] = field(default_factory=list)


def _prepare_lines(db: DBSession, lines: list[LineInput]) -> list[_PreparedLine]:
    if len(lines) < d.MIN_LINES_PER_ENTRY:
        raise _rule("too_few_lines", "A journal entry needs at least two lines")
    if len(lines) > d.MAX_LINES_PER_ENTRY:
        raise _rule("too_many_lines", "A journal entry has too many lines")
    prepared: list[_PreparedLine] = []
    for item in lines:
        account = db.get(Account, item.account_id)
        if account is None:
            raise _rule("invalid_account", "A line refers to an account that does not exist")
        debit = d.parse_money(item.debit, field="debit")
        credit = d.parse_money(item.credit, field="credit")
        d.validate_line(debit, credit)
        if item.tax_amount is not None and d.parse_money(item.tax_amount, field="tax_amount") < 0:
            raise _rule("negative_amount", "Tax amount must not be negative")
        prepared.append(_PreparedLine(account, debit, credit, item))
    return prepared


def _write_lines(db: DBSession, entry: JournalEntry, prepared: list[_PreparedLine]) -> None:
    for number, item in enumerate(prepared, start=1):
        src = item.source
        db.add(
            JournalLine(
                id=uuid.uuid4(),
                journal_entry_id=entry.id,
                line_number=number,
                account_id=item.account.id,
                debit=item.debit,
                credit=item.credit,
                description=(src.description or None),
                tax_name=src.tax_name,
                tax_type=src.tax_type,
                tax_rate=Decimal(src.tax_rate) if src.tax_rate is not None else None,
                tax_amount=(
                    d.parse_money(src.tax_amount, field="tax_amount") if src.tax_amount is not None else None
                ),
            )
        )


def list_entries(
    db: DBSession,
    *,
    status: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[JournalEntry], int]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    conditions = []
    if status is not None:
        conditions.append(JournalEntry.status == status)
    if start is not None:
        conditions.append(JournalEntry.transaction_date >= start)
    if end is not None:
        conditions.append(JournalEntry.transaction_date <= end)
    total = db.scalar(select(func.count()).select_from(JournalEntry).where(*conditions)) or 0
    stmt = (
        select(JournalEntry)
        .where(*conditions)
        .order_by(JournalEntry.transaction_date.desc(), JournalEntry.created_at.desc())
        .limit(limit)
        .offset(max(offset, 0))
    )
    return list(db.scalars(stmt)), int(total)


def get_entry(db: DBSession, entry_id: UUID) -> JournalEntry:
    entry = db.get(JournalEntry, entry_id)
    if entry is None:
        raise _not_found("Journal entry")
    return entry


def entry_lines(db: DBSession, entry_id: UUID) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine).where(JournalLine.journal_entry_id == entry_id).order_by(JournalLine.line_number)
        )
    )


def _entry_for_update(db: DBSession, entry_id: UUID) -> JournalEntry:
    entry = db.scalar(select(JournalEntry).where(JournalEntry.id == entry_id).with_for_update())
    if entry is None:
        raise _not_found("Journal entry")
    return entry


def create_draft(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    data: EntryDraftInput,
    *,
    commit: bool = True,
) -> JournalEntry:
    prepared = _prepare_lines(db, list(data.lines))
    entry = JournalEntry(
        id=uuid.uuid4(),
        transaction_date=data.transaction_date,
        description=data.description.strip(),
        reference_type=data.reference_type,
        reference_id=data.reference_id,
        status=d.EntryStatus.DRAFT.value,
        created_by_owner_id=auth.owner.id,
    )
    db.add(entry)
    _flush(db)
    _write_lines(db, entry, prepared)
    _audit(db, request, auth, "journal.created", "journal_entry", str(entry.id), {"lines": len(prepared)})
    _finish(db, commit)
    return entry


def update_draft(
    db: DBSession, request: Request, auth: AuthenticatedSession, entry_id: UUID, data: EntryDraftInput
) -> JournalEntry:
    entry = _entry_for_update(db, entry_id)
    if entry.status != d.EntryStatus.DRAFT.value:
        raise _rule("not_draft", "Only draft entries can be edited. Post a reversal to correct a posted entry.")
    prepared = _prepare_lines(db, list(data.lines))
    entry.transaction_date = data.transaction_date
    entry.description = data.description.strip()
    entry.reference_type = data.reference_type
    entry.reference_id = data.reference_id
    db.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry.id))
    _flush(db)
    db.expire(entry, ["lines"])
    _write_lines(db, entry, prepared)
    _audit(db, request, auth, "journal.updated", "journal_entry", str(entry.id), {"lines": len(prepared)})
    _persist(db)
    return entry


def delete_draft(db: DBSession, request: Request, auth: AuthenticatedSession, entry_id: UUID) -> None:
    entry = _entry_for_update(db, entry_id)
    if entry.status != d.EntryStatus.DRAFT.value:
        raise _rule("not_draft", "Posted entries cannot be deleted. Post a reversal instead.")
    db.execute(delete(JournalLine).where(JournalLine.journal_entry_id == entry.id))
    db.delete(entry)
    _audit(db, request, auth, "journal.draft_deleted", "journal_entry", str(entry_id))
    _persist(db)


def _post(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    entry: JournalEntry,
    posting_date: date,
    *,
    action: str,
    metadata: dict[str, object] | None = None,
    commit: bool = True,
) -> JournalEntry:
    lines = entry_lines(db, entry.id)
    amounts: list[d.LineAmounts] = []
    for line in lines:
        account = db.get(Account, line.account_id)
        if account is None or not account.is_active:
            raise _rule("inactive_account", "A line uses an account that is inactive or missing")
        has_children = db.scalar(select(Account.id).where(Account.parent_id == account.id).limit(1))
        if has_children is not None:
            raise _rule("header_account", "Header accounts cannot receive postings. Use a detail account.")
        amounts.append(d.LineAmounts(str(line.account_id), line.debit, line.credit))
    d.validate_entry(amounts)

    if _open_period_covering(db, posting_date) is None:
        raise _rule("no_open_period", "No OPEN accounting period covers this posting date")

    entry.posting_date = posting_date
    entry.entry_number = db.scalar(text("SELECT nextval('journal_entry_number_seq')"))
    entry.status = d.EntryStatus.POSTED.value
    _flush(db)
    _audit(
        db,
        request,
        auth,
        action,
        "journal_entry",
        str(entry.id),
        {"entry_number": entry.entry_number, **(metadata or {})},
    )
    _finish(db, commit)
    return entry


def post_entry(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    entry_id: UUID,
    *,
    posting_date: date | None = None,
    commit: bool = True,
) -> JournalEntry:
    entry = _entry_for_update(db, entry_id)
    if entry.status == d.EntryStatus.POSTED.value:
        raise _rule("already_posted", "This entry is already posted")
    return _post(
        db, request, auth, entry, posting_date or entry.transaction_date, action="journal.posted", commit=commit
    )


def reverse_entry(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    entry_id: UUID,
    *,
    posting_date: date | None = None,
    description: str | None = None,
    commit: bool = True,
) -> JournalEntry:
    """Post a mirror entry. The original stays untouched; the pair nets to zero."""
    original = _entry_for_update(db, entry_id)
    if original.status != d.EntryStatus.POSTED.value:
        raise _rule("not_posted", "Only posted entries can be reversed")
    if db.scalar(select(JournalEntry.id).where(JournalEntry.reverses_entry_id == original.id).limit(1)):
        raise _rule("already_reversed", "This entry has already been reversed")

    effective = posting_date or utcnow().date()
    reversal = JournalEntry(
        id=uuid.uuid4(),
        transaction_date=effective,
        description=(description or f"Reversal of entry {original.entry_number}: {original.description}")[:500],
        reference_type=original.reference_type,
        reference_id=original.reference_id,
        status=d.EntryStatus.DRAFT.value,
        reverses_entry_id=original.id,
        created_by_owner_id=auth.owner.id,
    )
    db.add(reversal)
    _flush(db)
    mirrored = [
        _PreparedLine(
            account=db.get(Account, line.account_id),  # type: ignore[arg-type]
            debit=line.credit,
            credit=line.debit,
            source=LineInput(
                account_id=line.account_id,
                debit="0",
                credit="0",
                description=line.description,
                tax_name=line.tax_name,
                tax_type=line.tax_type,
                tax_rate=str(line.tax_rate) if line.tax_rate is not None else None,
                tax_amount=str(line.tax_amount) if line.tax_amount is not None else None,
            ),
        )
        for line in entry_lines(db, original.id)
    ]
    _write_lines(db, reversal, mirrored)
    _flush(db)
    return _post(
        db,
        request,
        auth,
        reversal,
        effective,
        action="journal.reversed",
        metadata={"original_entry_number": original.entry_number, "original_entry_id": str(original.id)},
        commit=commit,
    )


# ---------------------------------------------------------------- reporting


@dataclass(frozen=True)
class AccountRow:
    totals: d.AccountTotals
    subtype: str
    is_active: bool


def _posted_conditions(start: date | None, end: date | None) -> list:
    conditions = [JournalEntry.status == d.EntryStatus.POSTED.value]
    if start is not None:
        conditions.append(JournalEntry.posting_date >= start)
    if end is not None:
        conditions.append(JournalEntry.posting_date <= end)
    return conditions


def account_rows(db: DBSession, start: date | None = None, end: date | None = None) -> list[AccountRow]:
    """Per-account debit and credit totals from POSTED lines in the posting-date range.

    Accounts with no activity are included with zero totals (the trial balance drops them).
    """
    lines = (
        select(JournalLine.account_id, JournalLine.debit, JournalLine.credit)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(*_posted_conditions(start, end))
        .subquery()
    )
    stmt = (
        select(
            Account.code,
            Account.name,
            Account.category,
            Account.subtype,
            Account.is_active,
            func.coalesce(func.sum(lines.c.debit), 0),
            func.coalesce(func.sum(lines.c.credit), 0),
        )
        .select_from(Account)
        .outerjoin(lines, lines.c.account_id == Account.id)
        .group_by(Account.id)
        .order_by(Account.code)
    )
    rows: list[AccountRow] = []
    for code, name, category, subtype, is_active, debit, credit in db.execute(stmt):
        rows.append(
            AccountRow(
                totals=d.AccountTotals(
                    code=code,
                    name=name,
                    category=d.AccountCategory(category),
                    debit_total=Decimal(debit),
                    credit_total=Decimal(credit),
                ),
                subtype=subtype,
                is_active=bool(is_active),
            )
        )
    return rows


def trial_balance(db: DBSession, start: date | None = None, end: date | None = None) -> d.TrialBalance:
    return d.trial_balance(row.totals for row in account_rows(db, start, end))


def profit_and_loss(db: DBSession, start: date | None = None, end: date | None = None) -> d.ProfitAndLoss:
    return d.profit_and_loss(row.totals for row in account_rows(db, start, end))


def balance_sheet(db: DBSession, as_of: date) -> d.BalanceSheet:
    return d.balance_sheet(row.totals for row in account_rows(db, None, as_of))


def cash_position(db: DBSession, as_of: date) -> tuple[list[dict[str, object]], Decimal]:
    items: list[dict[str, object]] = []
    total = Decimal("0.00")
    for row in account_rows(db, None, as_of):
        if row.subtype not in d.CASH_AND_BANK_SUBTYPES:
            continue
        balance = d.natural_balance(row.totals.category, row.totals.debit_total, row.totals.credit_total)
        total += balance
        items.append({"code": row.totals.code, "name": row.totals.name, "balance": balance})
    return items, total


@dataclass(frozen=True)
class LedgerResult:
    account: Account
    opening_balance: Decimal
    rows: list[d.LedgerLine]
    debit_total: Decimal
    credit_total: Decimal
    closing_balance: Decimal


def account_ledger(
    db: DBSession, account_id: UUID, start: date | None = None, end: date | None = None
) -> LedgerResult:
    account = get_account(db, account_id)
    category = d.AccountCategory(account.category)

    opening = Decimal("0.00")
    if start is not None:
        before = db.execute(
            select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalLine.account_id == account.id,
                JournalEntry.status == d.EntryStatus.POSTED.value,
                JournalEntry.posting_date < start,
            )
        ).one()
        opening = d.natural_balance(category, Decimal(before[0]), Decimal(before[1]))

    stmt = (
        select(
            JournalEntry.posting_date,
            JournalEntry.entry_number,
            JournalEntry.description,
            JournalLine.description,
            JournalLine.debit,
            JournalLine.credit,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalLine.account_id == account.id, *_posted_conditions(start, end))
        .order_by(JournalEntry.posting_date, JournalEntry.entry_number, JournalLine.line_number)
    )
    movements = [
        (posting_date, int(number), line_text or entry_text, Decimal(debit), Decimal(credit))
        for posting_date, number, entry_text, line_text, debit, credit in db.execute(stmt)
    ]
    rows = d.ledger_with_running_balance(category, movements, opening)
    debit_total = sum((row.debit for row in rows), Decimal("0.00"))
    credit_total = sum((row.credit for row in rows), Decimal("0.00"))
    closing = rows[-1].balance if rows else opening
    return LedgerResult(account, opening, rows, debit_total, credit_total, closing)


def dashboard(db: DBSession, today: date) -> dict[str, object]:
    drafts = db.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.status == d.EntryStatus.DRAFT.value)
    )
    posted = db.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.status == d.EntryStatus.POSTED.value)
    )
    period = _open_period_covering(db, today)
    cash_items, cash_total = cash_position(db, today)
    tb = trial_balance(db, None, today)
    sheet = balance_sheet(db, today)
    return {
        "draft_entries": int(drafts or 0),
        "posted_entries": int(posted or 0),
        "open_period": period.name if period else None,
        "cash_total": cash_total,
        "cash_accounts": len(cash_items),
        "trial_balance_balanced": tb.is_balanced,
        "balance_sheet_balanced": sheet.is_balanced,
    }


__all__ = [
    "AccountRow",
    "EntryDraftInput",
    "LedgerResult",
    "LineInput",
    "account_ledger",
    "account_rows",
    "balance_sheet",
    "cash_position",
    "close_period",
    "create_account",
    "create_default_chart",
    "create_draft",
    "create_period",
    "dashboard",
    "delete_draft",
    "entry_lines",
    "get_account",
    "get_entry",
    "list_accounts",
    "list_entries",
    "list_periods",
    "post_entry",
    "profit_and_loss",
    "reverse_entry",
    "trial_balance",
    "update_account",
    "update_draft",
]
