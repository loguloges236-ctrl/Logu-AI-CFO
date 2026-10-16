"""Database-backed finance service: debts, EMI schedules, payment obligations, payments, receivables, payables,
cash position, cash-flow forecast, upcoming obligations and the owner dashboard.

Rules:
- Arithmetic is Decimal and comes from app/finance/domain.py (pure). Nothing here calls an LLM.
- Helpers (`_...`) never commit. Public functions run inside `unit_of_work`, which commits or rolls back as a whole.
  Helpers may also be called from inside another unit of work (document approval does this).
- Every mutation writes an audit event with the actor, the target and the source job when one exists.
- Stored payments and settlements are append-only; the database enforces it. Mistakes are reversed, never deleted.
- Overdue detection looks back at most LOOKBACK_DAYS, so an old unpaid schedule cannot flood the view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.accounting.domain import CASH_AND_BANK_SUBTYPES
from app.finance.approval import DebtDraft, ObligationDraft
from app.finance.dashboard import DashboardInputs, build_dashboard
from app.finance.domain import (
    CRITICAL_KINDS,
    FREQUENCIES,
    INTEREST_TYPES,
    MAX_MONTHS,
    OBLIGATION_KINDS,
    DatedAmount,
    DebtPlan,
    FinanceRuleViolation,
    PayItem,
    bucket,
    build_schedule,
    compare_strategies,
    emi_amount,
    forecast,
    occurrences,
    parse_money,
    parse_rate,
    prioritise,
)
from app.models.accounting import Account
from app.models.commerce import Party, PurchaseBill, SalesInvoice
from app.models.finance import (
    BankAccount,
    Debt,
    DebtScheduleItem,
    PaymentObligation,
    PaymentRecord,
    ReconciliationSuggestion,
    Settlement,
)
from app.services import accounting as acc
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, unit_of_work
from app.settlements.domain import settlement_status

LOOKBACK_DAYS = 120
HORIZON_DAYS = 7
DEBT_KIND = "debt"
RECURRING_KINDS = tuple(k for k in OBLIGATION_KINDS if k not in ("emi", "interest", "salary"))
RECEIVABLE = "receivable"
PAYABLE = "payable"


def _rule(code: str, message: str) -> FinanceRuleViolation:
    return FinanceRuleViolation(code, message)


def _not_found(what: str) -> FinanceRuleViolation:
    return FinanceRuleViolation("not_found", f"{what} was not found")


def _text(value: object, field: str, *, max_len: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise _rule("missing_field", f"{field} is required")
    if len(text) > max_len:
        raise _rule("too_long", f"{field} must be at most {max_len} characters")
    return text


def _date(value: object, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _rule("invalid_date", f"{field} must be YYYY-MM-DD") from None


# ---------------------------------------------------------------- debts and EMI schedules

def _create_debt_rows(
    db: DBSession,
    request: Request,
    auth: AuthenticatedSession,
    *,
    name: str,
    lender: str,
    principal: Decimal,
    annual_rate: Decimal,
    interest_type: str,
    tenure_months: int,
    start_date: date,
    first_due_date: date | None = None,
    notes: str | None = None,
    source_job_id: UUID | None = None,
) -> Debt:
    if interest_type not in INTEREST_TYPES:
        raise _rule("invalid_interest_type", "Interest type must be reducing or flat")
    if not 1 <= tenure_months <= MAX_MONTHS:
        raise _rule("invalid_tenure", f"Tenure must be 1-{MAX_MONTHS} months")
    rows = build_schedule(principal, annual_rate, tenure_months, interest_type, start_date)
    emi = emi_amount(principal, annual_rate, tenure_months, interest_type)
    if first_due_date is not None and first_due_date != rows[0].due_date:
        # Installment n falls due on start + n months, so the first due date is fixed by start_date.
        raise _rule("first_due_mismatch", f"The first installment is due on {rows[0].due_date.isoformat()}")
    debt = Debt(
        name=name, lender=lender, principal=principal, annual_rate=annual_rate, interest_type=interest_type,
        emi=emi, tenure_months=tenure_months, start_date=start_date, first_due_date=rows[0].due_date,
        end_date=rows[-1].due_date, status="active", notes=notes, source_job_id=source_job_id,
    )
    db.add(debt)
    db.flush()
    for row in rows:
        db.add(DebtScheduleItem(
            debt_id=debt.id, installment_no=row.installment_no, due_date=row.due_date,
            opening_balance=row.opening, interest_amount=row.interest, principal_amount=row.principal,
            emi_amount=row.emi, closing_balance=row.closing,
        ))
    db.add(PaymentObligation(
        kind="emi", title=f"EMI - {name}"[:160], counterparty=lender[:160], amount=emi, rate=annual_rate,
        frequency="monthly", first_due_date=rows[0].due_date, end_date=rows[-1].due_date, is_critical=True,
        debt_id=debt.id, source_job_id=source_job_id,
    ))
    db.flush()
    audit(db, request, auth, "debt.created", "debt", str(debt.id), {
        "principal": str(principal), "tenure_months": tenure_months, "emi": str(emi),
        "source_job": str(source_job_id) if source_job_id else None,
    })
    return debt


def create_debt(db: DBSession, request: Request, auth: AuthenticatedSession, **fields: Any) -> Debt:
    name = _text(fields.get("name"), "Name", max_len=120)
    lender = _text(fields.get("lender"), "Lender", max_len=120)
    principal = parse_money(str(fields.get("principal")), "principal")
    if principal <= 0:
        raise _rule("invalid_amount", "Principal must be greater than zero")
    annual_rate = parse_rate(str(fields.get("annual_rate", "0")), "annual_rate")
    try:
        tenure = int(fields.get("tenure_months", 0))
    except (TypeError, ValueError):
        raise _rule("invalid_tenure", "Tenure must be a whole number of months") from None
    start = _date(fields.get("start_date"), "start_date")
    with unit_of_work(db):
        return _create_debt_rows(
            db, request, auth, name=name, lender=lender, principal=principal, annual_rate=annual_rate,
            interest_type=str(fields.get("interest_type", "reducing")), tenure_months=tenure, start_date=start,
            first_due_date=_date(fields["first_due_date"], "first_due_date") if fields.get("first_due_date") else None,
            notes=fields.get("notes"),
        )


def create_debt_from_draft(db: DBSession, request: Request, auth: AuthenticatedSession, draft: DebtDraft, source_job_id: UUID) -> Debt:
    """Used by document approval inside the approval's own unit of work."""
    return _create_debt_rows(
        db, request, auth, name=draft.name[:120], lender=draft.lender[:120], principal=draft.principal,
        annual_rate=draft.annual_rate, interest_type=draft.interest_type, tenure_months=draft.tenure_months,
        start_date=draft.start_date, first_due_date=draft.first_due_date, source_job_id=source_job_id,
    )


def get_debt(db: DBSession, debt_id: UUID) -> Debt:
    debt = db.get(Debt, debt_id)
    if debt is None:
        raise _not_found("Debt")
    return debt


def list_debts(db: DBSession) -> list[Debt]:
    return list(db.scalars(select(Debt).order_by(Debt.created_at.desc()).limit(200)))


def debt_progress(db: DBSession, debt: Debt, today: date) -> dict[str, Any]:
    """Schedule-based progress: the closing balance after the last installment due on or before today."""
    items = list(db.scalars(select(DebtScheduleItem).where(DebtScheduleItem.debt_id == debt.id).order_by(DebtScheduleItem.installment_no)))
    done = [i for i in items if i.due_date <= today]
    outstanding = done[-1].closing_balance if done else debt.principal
    interest_total = sum((i.interest_amount for i in items), Decimal("0"))
    return {
        "id": str(debt.id), "name": debt.name, "lender": debt.lender, "principal": str(debt.principal),
        "outstanding": str(outstanding), "emi": str(debt.emi), "annual_rate": str(debt.annual_rate),
        "interest_total": str(interest_total), "installments": len(items), "installments_paid_by_schedule": len(done),
        "next_due_date": next((i.due_date.isoformat() for i in items if i.due_date > today), None),
        "status": debt.status,
    }


# ---------------------------------------------------------------- obligations and payments

def create_obligation(db: DBSession, request: Request, auth: AuthenticatedSession, *, kind: str, title: str,
                      amount: str, frequency: str, first_due_date: date, end_date: date | None = None,
                      counterparty: str | None = None, source_job_id: UUID | None = None) -> PaymentObligation:
    if kind not in OBLIGATION_KINDS:
        raise _rule("invalid_kind", "Unknown obligation kind")
    if frequency not in FREQUENCIES:
        raise _rule("invalid_frequency", "Unknown frequency")
    value = parse_money(amount, "amount")
    if value <= 0:
        raise _rule("invalid_amount", "Amount must be greater than zero")
    if end_date is not None and end_date < first_due_date:
        raise _rule("invalid_dates", "End date cannot be before the first due date")
    with unit_of_work(db):
        return _create_obligation_row(db, request, auth, kind, title, value, frequency, first_due_date, end_date,
                                      counterparty, source_job_id)


def _create_obligation_row(db, request, auth, kind, title, value, frequency, first_due_date, end_date, counterparty,
                           source_job_id) -> PaymentObligation:
    row = PaymentObligation(
        kind=kind, title=_text(title, "Title", max_len=160), counterparty=(counterparty[:160] if counterparty else None),
        amount=value, frequency=frequency, first_due_date=first_due_date, end_date=end_date,
        is_critical=kind in CRITICAL_KINDS, source_job_id=source_job_id,
    )
    db.add(row)
    db.flush()
    audit(db, request, auth, "obligation.created", "payment_obligation", str(row.id),
          {"kind": kind, "amount": str(value), "frequency": frequency,
           "source_job": str(source_job_id) if source_job_id else None})
    return row


def create_obligation_from_draft(db, request, auth, draft: ObligationDraft, source_job_id: UUID) -> PaymentObligation:
    return _create_obligation_row(db, request, auth, draft.kind, draft.title, draft.amount, draft.frequency,
                                  draft.first_due_date, None, draft.counterparty, source_job_id)


def get_obligation(db: DBSession, obligation_id: UUID) -> PaymentObligation:
    row = db.get(PaymentObligation, obligation_id)
    if row is None:
        raise _not_found("Obligation")
    return row


def list_obligations(db: DBSession, *, kinds: tuple[str, ...] | None = None, exclude: tuple[str, ...] = ()) -> list[PaymentObligation]:
    query = select(PaymentObligation).where(PaymentObligation.status == "open")
    if kinds:
        query = query.where(PaymentObligation.kind.in_(kinds))
    if exclude:
        query = query.where(PaymentObligation.kind.not_in(exclude))
    return list(db.scalars(query.order_by(PaymentObligation.first_due_date).limit(500)))


def _period_paid(db: DBSession, obligation_id: UUID, period: date) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
            PaymentRecord.obligation_id == obligation_id, PaymentRecord.period_due_date == period
        )
    )
    return Decimal(total or 0)


def _check_payment_accounts(db: DBSession, cash_account_id: UUID, debit_account_id: UUID) -> None:
    cash = db.get(Account, cash_account_id)
    if cash is None or not cash.is_active:
        raise _not_found("Cash account")
    if cash.subtype not in CASH_AND_BANK_SUBTYPES:
        raise _rule("invalid_cash_account", "Payment must be paid from a cash or bank account")
    debit = db.get(Account, debit_account_id)
    if debit is None or not debit.is_active:
        raise _not_found("Debit account")
    if debit.id == cash.id:
        raise _rule("same_account", "Debit and cash accounts must differ")


def record_payment(db: DBSession, request: Request, auth: AuthenticatedSession, obligation_id: UUID, *,
                   period_due_date: date, amount: str, paid_on: date, cash_account_id: UUID,
                   debit_account_id: UUID, reference: str | None = None) -> PaymentRecord:
    """Records a payment and posts its journal (debit the obligation's account, credit cash) in the same transaction.
    A retry with the same reference returns the original payment and does not post a second journal."""
    value = parse_money(amount, "amount")
    if value <= 0:
        raise _rule("invalid_amount", "Payment must be greater than zero (use reversal to undo)")
    reference = ((reference or "").strip() or None)
    if reference is not None:
        reference = reference[:80]
    _check_payment_accounts(db, cash_account_id, debit_account_id)
    with unit_of_work(db):
        obligation = db.scalar(select(PaymentObligation).where(PaymentObligation.id == obligation_id).with_for_update())
        if obligation is None:
            raise _not_found("Obligation")
        if reference is not None:
            same = db.scalar(select(PaymentRecord).where(
                PaymentRecord.obligation_id == obligation.id, PaymentRecord.reference == reference,
                PaymentRecord.reverses_record_id.is_(None)))
            if same is not None:
                if same.period_due_date == period_due_date and same.amount == value and same.paid_on == paid_on:
                    return same
                raise _rule("reference_reused", "This reference was already used for a different payment")
        if obligation.status != "open":
            raise _rule("obligation_not_open", "This obligation is cancelled")
        due_dates = occurrences(obligation.first_due_date, obligation.frequency, period_due_date, period_due_date, obligation.end_date)
        if period_due_date not in due_dates:
            raise _rule("not_a_due_date", "That date is not a due date of this obligation")
        remaining = obligation.amount - _period_paid(db, obligation.id, period_due_date)
        if value > remaining:
            raise _rule("overpayment", f"Only {remaining} is still due for this period")

        payment_id = uuid4()
        label = f"Payment {obligation.title} ({period_due_date.isoformat()})"[:500]
        draft = acc.create_draft(
            db, request, auth,
            acc.EntryDraftInput(
                transaction_date=paid_on,
                description=label,
                reference_type="payment_record",
                reference_id=str(payment_id),
                lines=[
                    acc.LineInput(account_id=debit_account_id, debit=str(value), credit="0.00", description=label),
                    acc.LineInput(account_id=cash_account_id, debit="0.00", credit=str(value), description=label),
                ],
            ),
            commit=False,
        )
        posted = acc.post_entry(db, request, auth, draft.id, posting_date=paid_on, commit=False)
        row = PaymentRecord(id=payment_id, obligation_id=obligation.id, period_due_date=period_due_date, amount=value,
                            paid_on=paid_on, reference=reference, journal_entry_id=posted.id)
        db.add(row)
        db.flush()
        audit(db, request, auth, "payment.recorded", "payment_record", str(row.id), {
            "obligation": str(obligation.id), "period": period_due_date.isoformat(), "amount": str(value),
            "journal_entry": str(posted.id)})
    return row


def reverse_payment(db: DBSession, request: Request, auth: AuthenticatedSession, payment_id: UUID, *, paid_on: date) -> PaymentRecord:
    """Reverses a payment. If the payment was posted, its journal is reversed by a mirror entry in the same transaction."""
    with unit_of_work(db):
        original = db.scalar(select(PaymentRecord).where(PaymentRecord.id == payment_id).with_for_update())
        if original is None:
            raise _not_found("Payment")
        if original.amount <= 0 or original.reverses_record_id is not None:
            raise _rule("not_reversible", "Only an original payment can be reversed")
        already = db.scalar(select(PaymentRecord.id).where(PaymentRecord.reverses_record_id == original.id).limit(1))
        if already is not None:
            raise _rule("already_reversed", "This payment has already been reversed")
        journal_id = None
        if original.journal_entry_id is not None:
            mirror = acc.reverse_entry(db, request, auth, original.journal_entry_id, posting_date=paid_on,
                                       description=f"Reversal of payment {original.reference or original.id}"[:500],
                                       commit=False)
            journal_id = mirror.id
        row = PaymentRecord(obligation_id=original.obligation_id, period_due_date=original.period_due_date,
                            amount=-original.amount, paid_on=paid_on, reference=f"REV {original.id}"[:80],
                            reverses_record_id=original.id, journal_entry_id=journal_id)
        db.add(row)
        db.flush()
        audit(db, request, auth, "payment.reversed", "payment_record", str(row.id),
              {"original": str(original.id), "amount": str(original.amount), "journal_entry": str(journal_id)})
    return row


# ---------------------------------------------------------------- upcoming payments and receivable/payable views

@dataclass(frozen=True)
class DueRow:
    key: str
    kind: str
    title: str
    due: date
    amount: Decimal
    bucket: str
    obligation_id: str
    source: str  # "obligation" | "payable" | "receivable"
    rate: Decimal = Decimal("0")


def _open_due_rows(db: DBSession, today: date, horizon_days: int = HORIZON_DAYS) -> list[DueRow]:
    """Unpaid occurrences from LOOKBACK_DAYS ago to the horizon."""
    window_start = today - timedelta(days=LOOKBACK_DAYS)
    window_end = today + timedelta(days=horizon_days)
    rows: list[DueRow] = []
    for ob in list_obligations(db):
        for due in occurrences(ob.first_due_date, ob.frequency, window_start, window_end, ob.end_date):
            remaining = ob.amount - _period_paid(db, ob.id, due)
            if remaining <= 0:
                continue
            rows.append(DueRow(f"{ob.id}:{due.isoformat()}", ob.kind, ob.title, due, remaining, bucket(due, today),
                               str(ob.id), "obligation", ob.rate or Decimal("0")))
    return rows


def upcoming_obligations(db: DBSession, today: date, cash_available: Decimal | None = None) -> list[dict[str, Any]]:
    rows = _open_due_rows(db, today)
    if cash_available is None:
        cash_available = acc.cash_position(db, today)[1]
    items = [PayItem(r.key, r.amount, r.due, r.rate, r.kind) for r in rows]
    ranked = {p.key: p for p in prioritise(items, cash_available, today)}
    out = []
    for r in rows:
        p = ranked[r.key]
        out.append({"key": r.key, "kind": r.kind, "title": r.title, "due_date": r.due.isoformat(),
                    "amount": str(r.amount), "bucket": r.bucket, "obligation_id": r.obligation_id,
                    "rank": p.rank, "score": p.score, "affordable": p.affordable, "reasons": list(p.reasons)})
    out.sort(key=lambda x: x["rank"])
    return out


def _open_documents(db: DBSession, direction: str) -> list[dict[str, Any]]:
    if direction == RECEIVABLE:
        model, id_column = SalesInvoice, Settlement.sales_invoice_id
    else:
        model, id_column = PurchaseBill, Settlement.purchase_bill_id
    docs = list(db.scalars(select(model).where(model.status == "posted").order_by(model.due_date)))
    if not docs:
        return []
    totals = dict(db.execute(
        select(id_column, func.coalesce(func.sum(Settlement.amount), 0))
        .where(Settlement.direction == direction, id_column.is_not(None))
        .group_by(id_column)
    ).all())
    parties = {p.id: p.name for p in db.scalars(select(Party).where(Party.id.in_({d.party_id for d in docs})))}
    out = []
    for doc in docs:
        settled = Decimal(totals.get(doc.id, 0) or 0)
        status = settlement_status(doc.grand_total, settled)
        if status.outstanding == 0:
            continue
        out.append({
            "id": str(doc.id), "number": doc.number, "party": parties.get(doc.party_id, ""),
            "due_date": doc.due_date.isoformat() if doc.due_date else None,
            "total": str(doc.grand_total), "settled": str(settled), "outstanding": str(status.outstanding),
            "payment_status": status.status, "_due": doc.due_date, "_outstanding": status.outstanding,
        })
    return out


def receivables(db: DBSession, today: date) -> dict[str, Any]:
    return _summary(_open_documents(db, RECEIVABLE), today)


def payables(db: DBSession, today: date) -> dict[str, Any]:
    return _summary(_open_documents(db, PAYABLE), today)


def _summary(items: list[dict[str, Any]], today: date) -> dict[str, Any]:
    open_total = sum((i["_outstanding"] for i in items), Decimal("0"))
    overdue = [i for i in items if i["_due"] is not None and i["_due"] < today]
    overdue_total = sum((i["_outstanding"] for i in overdue), Decimal("0"))
    clean = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]
    return {"open_total": str(open_total), "overdue_total": str(overdue_total), "overdue_count": len(overdue),
            "items": clean}


# ---------------------------------------------------------------- cash, forecast, dashboard

def cash_position(db: DBSession, today: date) -> dict[str, Any]:
    items, total = acc.cash_position(db, today)
    bank_rows = list(db.scalars(select(BankAccount).where(BankAccount.is_active.is_(True))))
    bank_total = sum((b.provider_balance for b in bank_rows if b.provider_balance is not None), Decimal("0"))
    has_bank = any(b.provider_balance is not None for b in bank_rows)
    return {
        "ledger_cash_total": str(total),
        "ledger_accounts": [{"code": i["code"], "name": i["name"], "balance": str(i["balance"])} for i in items],
        "bank_balance_total": str(bank_total) if has_bank else None,
        "bank_accounts": [{"name": b.display_name, "balance": None if b.provider_balance is None else str(b.provider_balance),
                           "as_of": b.balance_as_of.isoformat() if b.balance_as_of else None} for b in bank_rows],
    }


def _forecast_inputs(db: DBSession, today: date) -> tuple[list[DatedAmount], list[DatedAmount]]:
    inflows = [DatedAmount(i["_due"] or today, i["_outstanding"], i["number"]) for i in _open_documents(db, RECEIVABLE)]
    outflows = [DatedAmount(i["_due"] or today, i["_outstanding"], i["number"]) for i in _open_documents(db, PAYABLE)]
    # Overdue rows are included on purpose: the forecast places them in week 1.
    for row in _open_due_rows(db, today, horizon_days=HORIZON_DAYS * 8):
        outflows.append(DatedAmount(row.due, row.amount, row.title))
    return inflows, outflows


def _forecast_weeks(db: DBSession, today: date, weeks: int) -> tuple[Decimal, list]:
    if not 1 <= weeks <= 26:
        raise _rule("invalid_weeks", "Weeks must be 1-26")
    opening = acc.cash_position(db, today)[1]
    inflows, outflows = _forecast_inputs(db, today)
    return opening, forecast(opening, inflows, outflows, today, weeks)


def cash_flow_forecast(db: DBSession, today: date, weeks: int = 8) -> dict[str, Any]:
    opening, weeks_out = _forecast_weeks(db, today, weeks)
    return {
        "opening_cash": str(opening),
        "expected_cash_in_4_weeks": str(weeks_out[min(3, len(weeks_out) - 1)].closing),
        "weeks": [{"week": w.week, "week_end": w.week_end.isoformat(), "inflow": str(w.inflow),
                   "outflow": str(w.outflow), "closing": str(w.closing), "negative": w.negative} for w in weeks_out],
        "note": "Projection from open receivables, open payables and unpaid obligations. Not a bank statement.",
    }


def _debt_outstanding(db: DBSession, today: date) -> tuple[Decimal, list[DebtPlan]]:
    plans: list[DebtPlan] = []
    total = Decimal("0")
    for debt in db.scalars(select(Debt).where(Debt.status == "active")):
        progress = debt_progress(db, debt, today)
        balance = Decimal(progress["outstanding"])
        total += balance
        if balance > 0:
            plans.append(DebtPlan(str(debt.id), balance, debt.annual_rate, debt.emi))
    return total, plans


def debt_strategy(db: DBSession, today: date, monthly_budget: Decimal) -> dict[str, Any]:
    _, plans = _debt_outstanding(db, today)
    if not plans:
        return {"monthly_budget": str(monthly_budget), "message": "No active debt"}
    result = compare_strategies(plans, monthly_budget)
    return _json(result)


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {k: _json(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


def dashboard(db: DBSession, today: date) -> dict[str, Any]:
    cash = acc.cash_position(db, today)[1]
    bank_rows = list(db.scalars(select(BankAccount).where(BankAccount.is_active.is_(True))))
    has_bank = any(b.provider_balance is not None for b in bank_rows)
    bank_total = sum((b.provider_balance for b in bank_rows if b.provider_balance is not None), Decimal("0")) if has_bank else None
    recv = receivables(db, today)
    pay = payables(db, today)
    debt_total, _ = _debt_outstanding(db, today)
    monthly_obligations = sum(
        (o.amount for o in list_obligations(db) if o.frequency == "monthly"), Decimal("0")
    )
    month_start = today.replace(day=1)
    pnl = acc.profit_and_loss(db, month_start, today)
    rows = _open_due_rows(db, today, horizon_days=HORIZON_DAYS)
    overdue = [r for r in rows if r.bucket == "overdue"]
    next_week_out = sum((r.amount for r in rows if r.due <= today + timedelta(days=HORIZON_DAYS)), Decimal("0"))
    unreconciled = db.scalar(
        select(func.count()).select_from(ReconciliationSuggestion).where(ReconciliationSuggestion.status == "suggested")
    ) or 0
    opening_cash, week_rows = _forecast_weeks(db, today, 8)
    inputs = DashboardInputs(
        cash_available=cash,
        bank_balance=bank_total,
        receivables_open=Decimal(recv["open_total"]),
        receivables_overdue=Decimal(recv["overdue_total"]),
        payables_open=Decimal(pay["open_total"]),
        payables_overdue=Decimal(pay["overdue_total"]),
        debt_outstanding=debt_total,
        monthly_obligations=monthly_obligations,
        monthly_income=pnl.income,
        monthly_expenses=pnl.expenses,
        next_seven_days_outflow=next_week_out,
        overdue_count=len(overdue),
        overdue_total=sum((r.amount for r in overdue), Decimal("0")),
        unreconciled_count=int(unreconciled),
        forecast=week_rows,
    )
    out = build_dashboard(inputs)
    out["expected_cash_in_4_weeks"] = str(week_rows[min(3, len(week_rows) - 1)].closing)
    out["upcoming"] = upcoming_obligations(db, today, cash)[:10]
    out["debt_burden_source"] = "monthly obligations / monthly income (ledger P&L for the current month)"
    return out

