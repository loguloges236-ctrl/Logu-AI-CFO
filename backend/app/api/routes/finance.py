"""Authenticated finance API: debts, EMI, interest, salary, recurring payments, receivables, payables, settlements,
read-only bank data, reconciliation, cash position, forecast, dashboard, reminders and the AI CFO question endpoint.

Every route requires the owner session (get_current_auth). Every mutation also requires the CSRF check (require_csrf).
Errors are stable codes; internal details, tokens and provider credentials are never returned.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session as DBSession

from app.accounting.domain import AccountingRuleViolation
from app.ai.cfo import REFUSAL, route_question
from app.ai.cfo import AIRuleViolation
from app.ai.executor import execute_tool
from app.bank.providers import BankDataProvider, UnavailableBankProvider
from app.core.errors import APIError
from app.db.session import get_db
from app.finance.domain import FinanceRuleViolation, parse_money
from app.schemas import finance as sf
from app.services import bank as bank_service
from app.services import finance as fin
from app.services import notifications as notify
from app.services import settlements as settle
from app.services.auth import AuthenticatedSession, get_current_auth, require_csrf
from app.services.commerce_common import CommerceRuleViolation
from app.services.finance_ai import DBCFOReader

router = APIRouter(prefix="/finance", tags=["finance"])

_CONFLICT = frozenset({
    "overpayment", "obligation_not_open", "already_reversed", "already_decided", "already_confirmed",
    "reference_reused", "not_reversible", "not_posted", "no_ledger_account", "not_confirmable",
    "first_due_mismatch",
})


def get_bank_provider() -> BankDataProvider:
    """No bank is connected in this codebase. Tests replace this dependency with the fixture provider."""
    return UnavailableBankProvider()


@contextmanager
def _translate() -> Iterator[None]:
    try:
        yield
    except FinanceRuleViolation as error:
        if error.code == "not_found":
            raise APIError(status.HTTP_404_NOT_FOUND, "not_found", error.message) from None
        code = status.HTTP_409_CONFLICT if error.code in _CONFLICT else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise APIError(code, error.code, error.message) from None
    except (CommerceRuleViolation, AccountingRuleViolation) as error:
        if error.code == "not_found":
            raise APIError(status.HTTP_404_NOT_FOUND, "not_found", error.message) from None
        raise APIError(status.HTTP_409_CONFLICT if error.code in _CONFLICT else status.HTTP_422_UNPROCESSABLE_ENTITY,
                       error.code, error.message) from None


def _today() -> date:
    from app.core.clock import utcnow

    return utcnow().date()


# ---------------------------------------------------------------- overview

@router.get("/dashboard")
def dashboard(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.dashboard(db, _today())


@router.get("/cash-position")
def cash_position(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.cash_position(db, _today())


@router.get("/forecast")
def forecast(weeks: int = Query(default=8, ge=1, le=26), auth: AuthenticatedSession = Depends(get_current_auth),
             db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.cash_flow_forecast(db, _today(), weeks)


@router.get("/upcoming")
def upcoming(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return fin.upcoming_obligations(db, _today())


# ---------------------------------------------------------------- debts and EMI schedules

@router.get("/debts")
def list_debts(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return [fin.debt_progress(db, d, _today()) for d in fin.list_debts(db)]


@router.post("/debts", status_code=status.HTTP_201_CREATED)
def create_debt(body: sf.DebtIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        debt = fin.create_debt(db, request, auth, **body.model_dump())
        return fin.debt_progress(db, debt, _today())


@router.get("/debts/{debt_id}")
def get_debt(debt_id: UUID, auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        debt = fin.get_debt(db, debt_id)
        return {**fin.debt_progress(db, debt, _today()), "schedule": [
            {"installment": i.installment_no, "due_date": i.due_date.isoformat(), "opening": str(i.opening_balance),
             "interest": str(i.interest_amount), "principal": str(i.principal_amount), "emi": str(i.emi_amount),
             "closing": str(i.closing_balance)}
            for i in _schedule(db, debt_id)]}


def _schedule(db: DBSession, debt_id: UUID):
    from sqlalchemy import select

    from app.models.finance import DebtScheduleItem

    return list(db.scalars(select(DebtScheduleItem).where(DebtScheduleItem.debt_id == debt_id)
                           .order_by(DebtScheduleItem.installment_no).limit(600)))


@router.get("/debt-strategy")
def debt_strategy(monthly_budget: str = Query(pattern=sf.MONEY_PATTERN), auth: AuthenticatedSession = Depends(get_current_auth),
                  db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.debt_strategy(db, _today(), parse_money(monthly_budget, "monthly_budget"))


# ---------------------------------------------------------------- obligations by kind

def _obligations(db: DBSession, kinds: tuple[str, ...] | None = None, exclude: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [{"id": str(o.id), "kind": o.kind, "title": o.title, "counterparty": o.counterparty, "amount": str(o.amount),
             "frequency": o.frequency, "first_due_date": o.first_due_date.isoformat(),
             "end_date": o.end_date.isoformat() if o.end_date else None, "is_critical": o.is_critical,
             "status": o.status, "debt_id": str(o.debt_id) if o.debt_id else None}
            for o in fin.list_obligations(db, kinds=kinds, exclude=exclude)]


@router.get("/emis")
def emis(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return _obligations(db, kinds=("emi",))


@router.get("/interest")
def interest(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return _obligations(db, kinds=("interest",))


@router.get("/salaries")
def salaries(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return _obligations(db, kinds=("salary",))


@router.get("/recurring")
def recurring(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return _obligations(db, exclude=("emi", "interest", "salary"))


@router.post("/obligations", status_code=status.HTTP_201_CREATED)
def create_obligation(body: sf.ObligationIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                      db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = fin.create_obligation(db, request, auth, **body.model_dump())
        return {"id": str(row.id), "kind": row.kind, "amount": str(row.amount)}


@router.post("/obligations/{obligation_id}/payments", status_code=status.HTTP_201_CREATED)
def record_payment(obligation_id: UUID, body: sf.PaymentIn, request: Request,
                   auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = fin.record_payment(db, request, auth, obligation_id, period_due_date=body.period_due_date,
                                 amount=body.amount, paid_on=body.paid_on, cash_account_id=body.cash_account_id,
                                 debit_account_id=body.debit_account_id, reference=body.reference)
        return {"id": str(row.id), "amount": str(row.amount), "period_due_date": row.period_due_date.isoformat(),
                "journal_entry_id": str(row.journal_entry_id)}


@router.post("/payments/{payment_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_payment(payment_id: UUID, body: sf.ReverseIn, request: Request,
                    auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = fin.reverse_payment(db, request, auth, payment_id, paid_on=body.paid_on)
        return {"id": str(row.id), "amount": str(row.amount)}


# ---------------------------------------------------------------- receivables, payables, settlements

@router.get("/receivables")
def receivables(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.receivables(db, _today())


@router.get("/payables")
def payables(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return fin.payables(db, _today())


@router.get("/settlements")
def list_settlements(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return settle.list_settlements(db)


@router.post("/settlements", status_code=status.HTTP_201_CREATED)
def approve_settlement(body: sf.SettlementIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                       db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = settle.approve_settlement(db, request, auth, **body.model_dump())
        return {"id": str(row.id), "amount": str(row.amount), "journal_entry_id": str(row.journal_entry_id), "state": "completed"}


@router.post("/settlements/{settlement_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse_settlement(settlement_id: UUID, body: sf.SettlementReverseIn, request: Request,
                       auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = settle.reverse_settlement(db, request, auth, settlement_id, reversed_on=body.reversed_on, reason=body.reason)
        return {"id": str(row.id), "amount": str(row.amount), "journal_entry_id": str(row.journal_entry_id), "state": "reversal"}


@router.post("/settlement-requests", status_code=status.HTTP_201_CREATED)
def create_settlement_request(body: sf.SettlementIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                              db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = settle.request_settlement(db, request, auth, **body.model_dump())
        return {"id": str(row.id), "status": row.status, "amount": str(row.amount)}


@router.get("/settlement-requests")
def list_settlement_requests(status_filter: str | None = Query(default=None, alias="status"),
                             auth: AuthenticatedSession = Depends(get_current_auth),
                             db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return settle.list_requests(db, status=status_filter)


@router.post("/settlement-requests/{request_id}/approve", status_code=status.HTTP_201_CREATED)
def approve_settlement_request(request_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                               db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = settle.approve_settlement_request(db, request, auth, request_id)
        return {"id": str(row.id), "amount": str(row.amount), "journal_entry_id": str(row.journal_entry_id), "state": "completed"}


@router.post("/settlement-requests/{request_id}/reject")
def reject_settlement_request(request_id: UUID, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                              db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = settle.reject_settlement_request(db, request, auth, request_id)
        return {"id": str(row.id), "status": row.status}


# ---------------------------------------------------------------- bank (read-only)

@router.get("/bank/accounts")
def bank_accounts(auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.models.finance import BankAccount

    rows = db.scalars(select(BankAccount).order_by(BankAccount.display_name).limit(100))
    return [{"id": str(r.id), "display_name": r.display_name, "provider": r.provider, "currency": r.currency,
             "balance": None if r.provider_balance is None else str(r.provider_balance),
             "as_of": r.balance_as_of.isoformat() if r.balance_as_of else None,
             "ledger_linked": r.ledger_account_id is not None, "read_only": True} for r in rows]


@router.post("/bank/accounts/import")
def import_bank_accounts(request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                         db: DBSession = Depends(get_db), provider: BankDataProvider = Depends(get_bank_provider)) -> dict[str, Any]:
    from app.bank.providers import BankProviderUnavailable

    try:
        return {"accounts": bank_service.import_accounts(db, request, auth, provider), "read_only": True}
    except BankProviderUnavailable:
        raise APIError(status.HTTP_503_SERVICE_UNAVAILABLE, "bank_provider_unavailable",
                       "No bank provider is connected") from None


@router.post("/bank/accounts/{bank_account_id}/ledger-link")
def link_ledger(bank_account_id: UUID, body: sf.LedgerLinkIn, request: Request,
                auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = bank_service.link_ledger_account(db, request, auth, bank_account_id, body.ledger_account_id)
        return {"id": str(row.id), "ledger_linked": True}


@router.get("/bank/accounts/{bank_account_id}/transactions")
def bank_transactions(bank_account_id: UUID, since: date, until: date, auth: AuthenticatedSession = Depends(get_current_auth),
                      db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return bank_service.list_transactions(db, bank_account_id, since=since, until=until)


@router.post("/bank/accounts/{bank_account_id}/transactions/import")
def import_bank_transactions(bank_account_id: UUID, body: sf.ImportWindowIn, request: Request,
                             auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db),
                             provider: BankDataProvider = Depends(get_bank_provider)) -> dict[str, Any]:
    from app.bank.providers import BankProviderUnavailable

    try:
        with _translate():
            return {**bank_service.import_transactions(db, request, auth, provider, bank_account_id,
                                                       since=body.since, until=body.until), "read_only": True}
    except BankProviderUnavailable:
        raise APIError(status.HTTP_503_SERVICE_UNAVAILABLE, "bank_provider_unavailable",
                       "No bank provider is connected") from None


# ---------------------------------------------------------------- reconciliation

@router.post("/reconciliation/run")
def run_reconciliation(body: sf.ReconcileIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
                       db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return bank_service.reconcile_account(db, request, auth, body.bank_account_id, since=body.since, until=body.until)


@router.get("/reconciliation/suggestions")
def suggestions(status_filter: str = Query(default="suggested", alias="status"),
                auth: AuthenticatedSession = Depends(get_current_auth), db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    with _translate():
        return bank_service.list_suggestions(db, status=status_filter)


@router.post("/reconciliation/suggestions/{suggestion_id}/decision")
def decide(suggestion_id: UUID, body: sf.DecisionIn, request: Request, auth: AuthenticatedSession = Depends(require_csrf),
           db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        row = bank_service.decide_suggestion(db, request, auth, suggestion_id, body.decision)
        return {"id": str(row.id), "status": row.status}


# ---------------------------------------------------------------- notifications and reminders

@router.get("/notifications")
def notifications(unread_only: bool = False, auth: AuthenticatedSession = Depends(get_current_auth),
                  db: DBSession = Depends(get_db)) -> list[dict[str, Any]]:
    return notify.list_notifications(db, unread_only=unread_only)


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: UUID, auth: AuthenticatedSession = Depends(require_csrf),
                      db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        notify.mark_read(db, notification_id)
    return {"read": True}


@router.post("/reminders/run")
def run_reminders(request: Request, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    with _translate():
        return notify.run_reminders(db, request, auth, _today())


# ---------------------------------------------------------------- AI CFO (read-only)

@router.post("/ai/ask")
def ask(body: sf.AskIn, auth: AuthenticatedSession = Depends(require_csrf), db: DBSession = Depends(get_db)) -> dict[str, Any]:
    route = route_question(body.question)
    if route.kind != "tool":
        return {"answer": route.message or REFUSAL, "tool": None, "read_only": True}
    try:
        result = execute_tool(route.tool, route.args, DBCFOReader(db, _today()))
    except AIRuleViolation as error:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, error.code, error.message) from None
    return {"answer": result["result"], "tool": result["tool"], "args": result["args"], "read_only": True}
