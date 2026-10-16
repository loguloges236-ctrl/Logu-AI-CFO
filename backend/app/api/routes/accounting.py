"""Authenticated accounting API. Every route requires the owner session; every mutation also
requires the CSRF token. There are no public routes and no endpoint accepts raw SQL or filters.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session as DBSession

from app.accounting import domain as d
from app.core.clock import utcnow
from app.core.errors import APIError
from app.db.session import get_db
from app.models import Account, AccountingPeriod, JournalEntry
from app.schemas.accounting import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BalanceSheetOut,
    CashPositionOut,
    DashboardOut,
    EntryIn,
    EntryListOut,
    EntryOut,
    LedgerOut,
    PeriodIn,
    PeriodOut,
    PostIn,
    ProfitAndLossOut,
    ReverseIn,
    SetupOut,
    TrialBalanceOut,
)
from app.services import accounting as svc
from app.services.auth import AuthenticatedSession, get_current_auth, require_csrf

router = APIRouter(prefix="/accounting", tags=["accounting"])

_NOT_FOUND = "not_found"
_CONFLICT_CODES = frozenset(
    {
        "duplicate_code",
        "duplicate_period_name",
        "period_overlap",
        "already_posted",
        "not_draft",
        "not_posted",
        "already_reversed",
        "already_closed",
        "no_open_period",
        "already_initialized",
        "database_rule",
    }
)


@contextmanager
def _translate() -> Iterator[None]:
    """Convert domain rule failures into stable, client-safe API errors."""
    try:
        yield
    except d.AccountingRuleViolation as error:
        if error.code == _NOT_FOUND:
            raise APIError(status.HTTP_404_NOT_FOUND, _NOT_FOUND, error.message) from None
        http_status = status.HTTP_409_CONFLICT if error.code in _CONFLICT_CODES else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise APIError(http_status, error.code, error.message) from None


def _money(value: Any) -> str | None:
    return None if value is None else d.money_text(value)


def _account_out(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "code": account.code,
        "name": account.name,
        "category": account.category,
        "subtype": account.subtype,
        "description": account.description,
        "parent_id": account.parent_id,
        "is_active": account.is_active,
        "is_system": account.is_system,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def _entry_out(db: DBSession, entry: JournalEntry) -> dict[str, Any]:
    lines = []
    for line in svc.entry_lines(db, entry.id):
        account = db.get(Account, line.account_id)
        lines.append(
            {
                "id": line.id,
                "line_number": line.line_number,
                "account_id": line.account_id,
                "account_code": account.code if account else "",
                "debit": d.money_text(line.debit),
                "credit": d.money_text(line.credit),
                "description": line.description,
                "tax_name": line.tax_name,
                "tax_type": line.tax_type,
                "tax_rate": str(line.tax_rate) if line.tax_rate is not None else None,
                "tax_amount": _money(line.tax_amount),
            }
        )
    return {
        "id": entry.id,
        "entry_number": entry.entry_number,
        "transaction_date": entry.transaction_date,
        "posting_date": entry.posting_date,
        "reference_type": entry.reference_type,
        "reference_id": entry.reference_id,
        "description": entry.description,
        "status": entry.status,
        "reverses_entry_id": entry.reverses_entry_id,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "lines": lines,
    }


def _draft_input(body: EntryIn) -> svc.EntryDraftInput:
    return svc.EntryDraftInput(
        transaction_date=body.transaction_date,
        description=body.description,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
        lines=[
            svc.LineInput(
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
                tax_name=line.tax_name,
                tax_type=line.tax_type,
                tax_rate=line.tax_rate,
                tax_amount=line.tax_amount,
            )
            for line in body.lines
        ],
    )


def _check_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None:
        with _translate():
            d.validate_period(start, end)


# ---------------------------------------------------------------- chart of accounts


@router.post("/setup", response_model=SetupOut, status_code=status.HTTP_201_CREATED)
def setup_default_chart(
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, int]:
    with _translate():
        created = svc.create_default_chart(db, request, auth)
    return {"created": created}


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(
    include_inactive: bool = True,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [_account_out(account) for account in svc.list_accounts(db, include_inactive=include_inactive)]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountCreate,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        account = svc.create_account(
            db,
            request,
            auth,
            code=body.code,
            name=body.name,
            category=body.category,
            subtype=body.subtype,
            description=body.description,
            parent_id=body.parent_id,
        )
    return _account_out(account)


@router.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(
    account_id: UUID,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        return _account_out(svc.get_account(db, account_id))


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: UUID,
    body: AccountUpdate,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        account = svc.update_account(
            db,
            request,
            auth,
            account_id,
            name=body.name,
            description=body.description,
            subtype=body.subtype,
            is_active=body.is_active,
        )
    return _account_out(account)


@router.get("/accounts/{account_id}/ledger", response_model=LedgerOut)
def account_ledger(
    account_id: UUID,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    _check_range(start, end)
    with _translate():
        result = svc.account_ledger(db, account_id, start, end)
    return {
        "account_id": result.account.id,
        "account_code": result.account.code,
        "account_name": result.account.name,
        "category": result.account.category,
        "opening_balance": d.money_text(result.opening_balance),
        "debit_total": d.money_text(result.debit_total),
        "credit_total": d.money_text(result.credit_total),
        "closing_balance": d.money_text(result.closing_balance),
        "rows": [
            {
                "posting_date": row.posting_date,
                "entry_number": row.entry_number,
                "description": row.description,
                "debit": d.money_text(row.debit),
                "credit": d.money_text(row.credit),
                "balance": d.money_text(row.balance),
            }
            for row in result.rows
        ],
    }


# ---------------------------------------------------------------- journal entries


@router.get("/journal-entries", response_model=EntryListOut)
def list_entries(
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(draft|posted)$"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    _check_range(start, end)
    entries, total = svc.list_entries(
        db, status=status_filter, start=start, end=end, limit=limit, offset=offset
    )
    return {
        "items": [_entry_out(db, entry) for entry in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/journal-entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(
    body: EntryIn,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        entry = svc.create_draft(db, request, auth, _draft_input(body))
    return _entry_out(db, entry)


@router.get("/journal-entries/{entry_id}", response_model=EntryOut)
def get_entry(
    entry_id: UUID,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        entry = svc.get_entry(db, entry_id)
    return _entry_out(db, entry)


@router.put("/journal-entries/{entry_id}", response_model=EntryOut)
def update_entry(
    entry_id: UUID,
    body: EntryIn,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        entry = svc.update_draft(db, request, auth, entry_id, _draft_input(body))
    return _entry_out(db, entry)


@router.delete(
    "/journal-entries/{entry_id}",
    status_code=status.HTTP_200_OK,
    response_class=Response,
)
def delete_entry(
    entry_id: UUID,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> None:
    with _translate():
        svc.delete_draft(db, request, auth, entry_id)


@router.post("/journal-entries/{entry_id}/post", response_model=EntryOut)
def post_entry(
    entry_id: UUID,
    request: Request,
    body: PostIn | None = None,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        entry = svc.post_entry(db, request, auth, entry_id, posting_date=(body.posting_date if body else None))
    return _entry_out(db, entry)


@router.post("/journal-entries/{entry_id}/reverse", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def reverse_entry(
    entry_id: UUID,
    request: Request,
    body: ReverseIn | None = None,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    with _translate():
        entry = svc.reverse_entry(
            db,
            request,
            auth,
            entry_id,
            posting_date=(body.posting_date if body else None),
            description=(body.description if body else None),
        )
    return _entry_out(db, entry)


# ---------------------------------------------------------------- periods


@router.get("/periods", response_model=list[PeriodOut])
def list_periods(
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> list[AccountingPeriod]:
    return svc.list_periods(db)


@router.post("/periods", response_model=PeriodOut, status_code=status.HTTP_201_CREATED)
def create_period(
    body: PeriodIn,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> AccountingPeriod:
    with _translate():
        return svc.create_period(
            db, request, auth, name=body.name, start_date=body.start_date, end_date=body.end_date
        )


@router.post("/periods/{period_id}/close", response_model=PeriodOut)
def close_period(
    period_id: UUID,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> AccountingPeriod:
    with _translate():
        return svc.close_period(db, request, auth, period_id)


# ---------------------------------------------------------------- reports


@router.get("/trial-balance", response_model=TrialBalanceOut)
def trial_balance(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    _check_range(start, end)
    tb = svc.trial_balance(db, start, end)
    return {
        "rows": [
            {
                "code": row.code,
                "name": row.name,
                "category": row.category.value,
                "debit_balance": d.money_text(row.debit_balance),
                "credit_balance": d.money_text(row.credit_balance),
            }
            for row in tb.rows
        ],
        "total_debit": d.money_text(tb.total_debit),
        "total_credit": d.money_text(tb.total_credit),
        "is_balanced": tb.is_balanced,
    }


@router.get("/reports/profit-and-loss", response_model=ProfitAndLossOut)
def profit_and_loss(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    _check_range(start, end)
    pnl = svc.profit_and_loss(db, start, end)
    return {
        "income": d.money_text(pnl.income),
        "expenses": d.money_text(pnl.expenses),
        "net_profit": d.money_text(pnl.net_profit),
    }


@router.get("/reports/balance-sheet", response_model=BalanceSheetOut)
def balance_sheet(
    as_of: date | None = Query(default=None),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    sheet = svc.balance_sheet(db, as_of or utcnow().date())
    return {
        "assets": d.money_text(sheet.assets),
        "liabilities": d.money_text(sheet.liabilities),
        "equity": d.money_text(sheet.equity),
        "current_earnings": d.money_text(sheet.current_earnings),
        "liabilities_and_equity": d.money_text(sheet.liabilities_and_equity),
        "is_balanced": sheet.is_balanced,
    }


@router.get("/reports/cash-position", response_model=CashPositionOut)
def cash_position(
    as_of: date | None = Query(default=None),
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    items, total = svc.cash_position(db, as_of or utcnow().date())
    return {
        "items": [
            {"code": str(item["code"]), "name": str(item["name"]), "balance": d.money_text(item["balance"])}  # type: ignore[arg-type]
            for item in items
        ],
        "total": d.money_text(total),
    }


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    data = svc.dashboard(db, utcnow().date())
    return {
        **data,
        "cash_total": d.money_text(data["cash_total"]),  # type: ignore[arg-type]
    }
