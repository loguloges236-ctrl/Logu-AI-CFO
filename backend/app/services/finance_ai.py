"""Read-only data for the AI CFO. It implements the CFOReader protocol with getters only.

The LLM never receives this object. The executor (app/ai/executor.py) calls one getter per allow-listed tool, and each
getter returns plain strings and numbers computed by the deterministic services above. No SQL is accepted from the
model, and nothing here writes, posts, pays or changes stock.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.models.finance import BankTransaction, DebtScheduleItem, ReconciliationSuggestion
from app.services import accounting as acc
from app.services import finance as fin


class DBCFOReader:
    def __init__(self, db: DBSession, today: date) -> None:
        self._db = db
        self._today = today

    def cash_position(self) -> dict[str, Any]:
        return fin.cash_position(self._db, self._today)

    def upcoming_payments(self, scope: str) -> list[dict[str, Any]]:
        rows = fin.upcoming_obligations(self._db, self._today)
        wanted = {
            "today": {"today", "overdue"}, "tomorrow": {"tomorrow"}, "week": {"today", "tomorrow", "this_week", "overdue"},
            "overdue": {"overdue"}, "all": None,
        }[scope]
        return [r for r in rows if wanted is None or r["bucket"] in wanted][:20]

    def receivables(self) -> dict[str, Any]:
        return fin.receivables(self._db, self._today)

    def payables(self) -> dict[str, Any]:
        return fin.payables(self._db, self._today)

    def debt_summary(self) -> dict[str, Any]:
        total, _ = fin._debt_outstanding(self._db, self._today)
        monthly = sum((o.amount for o in fin.list_obligations(self._db, kinds=("emi",)) if o.frequency == "monthly"), Decimal("0"))
        return {"debt_outstanding": str(total), "monthly_emi": str(monthly),
                "interest_cost_this_month": str(_interest_this_month(self._db, self._today))}

    def debt_strategy(self, monthly_budget: str) -> dict[str, Any]:
        return fin.debt_strategy(self._db, self._today, Decimal(monthly_budget))

    def period_summary(self, metric: str) -> dict[str, Any]:
        pnl = acc.profit_and_loss(self._db, self._today.replace(day=1), self._today)
        value = {"sales": pnl.income, "expenses": pnl.expenses, "profit": pnl.net_profit}[metric]
        return {"metric": metric, "value": str(value), "from": self._today.replace(day=1).isoformat(),
                "to": self._today.isoformat()}

    def top_expenses(self) -> list[dict[str, Any]]:
        rows = acc.account_rows(self._db, self._today.replace(day=1), self._today)
        items = []
        for row in rows:
            if row.totals.category.value != "expense":
                continue
            net = row.totals.debit_total - row.totals.credit_total
            if net > 0:
                items.append({"code": row.totals.code, "name": row.totals.name, "amount": str(net)})
        items.sort(key=lambda i: Decimal(i["amount"]), reverse=True)
        return items[:5]

    def cash_movement(self) -> list[dict[str, Any]]:
        since = self._today.replace(day=1)
        rows = list(self._db.scalars(
            select(BankTransaction).where(BankTransaction.txn_date >= since, BankTransaction.txn_date <= self._today,
                                          BankTransaction.amount < 0)
            .order_by(BankTransaction.amount).limit(5)))
        return [{"date": r.txn_date.isoformat(), "amount": str(r.amount), "description": r.description} for r in rows]

    def risk_alerts(self) -> list[dict[str, Any]]:
        return fin.dashboard(self._db, self._today)["alerts"]

    def priority_list(self) -> list[dict[str, Any]]:
        return fin.upcoming_obligations(self._db, self._today)[:10]

    def reconciliation_status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for status in ("suggested", "confirmed", "rejected"):
            counts[status] = int(self._db.scalar(
                select(func.count()).select_from(ReconciliationSuggestion)
                .where(ReconciliationSuggestion.status == status)) or 0)
        return counts


def _interest_this_month(db: DBSession, today: date) -> Decimal:
    total = db.scalar(select(func.coalesce(func.sum(DebtScheduleItem.interest_amount), 0)).where(
        DebtScheduleItem.due_date >= today.replace(day=1), DebtScheduleItem.due_date <= today))
    return Decimal(total or 0)
