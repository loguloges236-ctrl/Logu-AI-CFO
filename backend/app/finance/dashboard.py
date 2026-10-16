"""Owner dashboard figures. Pure: the caller passes figures read from the database; this module only combines them.

Nothing here reads a database or calls a model. Money is Decimal throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.finance.domain import ForecastWeek, debt_burden, risk_alerts

CENT = Decimal("0.01")


@dataclass(frozen=True)
class DashboardInputs:
    cash_available: Decimal
    bank_balance: Decimal | None
    receivables_open: Decimal
    receivables_overdue: Decimal
    payables_open: Decimal
    payables_overdue: Decimal
    debt_outstanding: Decimal
    monthly_obligations: Decimal
    monthly_income: Decimal
    monthly_expenses: Decimal
    next_seven_days_outflow: Decimal
    overdue_count: int
    overdue_total: Decimal
    unreconciled_count: int
    forecast: list[ForecastWeek] = field(default_factory=list)


def build_dashboard(data: DashboardInputs) -> dict[str, object]:
    for name in ("cash_available", "receivables_open", "payables_open", "debt_outstanding", "monthly_expenses"):
        value = getattr(data, name)
        if value < 0 and name not in ("cash_available",):
            raise ValueError(f"{name} cannot be negative")
    net_cash_flow = (data.monthly_income - data.monthly_expenses).quantize(CENT, rounding=ROUND_HALF_UP)
    negative_week = next((w.week for w in data.forecast if w.negative), None)
    alerts = risk_alerts(
        cash_available=data.cash_available,
        overdue_count=data.overdue_count,
        overdue_total=data.overdue_total,
        next_seven_days_outflow=data.next_seven_days_outflow,
        monthly_income=data.monthly_income,
        monthly_obligations=data.monthly_obligations,
        receivables_overdue_total=data.receivables_overdue,
        forecast_negative_week=negative_week,
        unreconciled_count=data.unreconciled_count,
    )
    return {
        "cash_available": str(data.cash_available),
        "bank_balance": None if data.bank_balance is None else str(data.bank_balance),
        "receivables_open": str(data.receivables_open),
        "receivables_overdue": str(data.receivables_overdue),
        "payables_open": str(data.payables_open),
        "payables_overdue": str(data.payables_overdue),
        "debt_outstanding": str(data.debt_outstanding),
        "monthly_obligations": str(data.monthly_obligations),
        "monthly_income": str(data.monthly_income),
        "monthly_expenses": str(data.monthly_expenses),
        "net_cash_flow": str(net_cash_flow),
        "debt_burden": None if (r := debt_burden(data.monthly_obligations, data.monthly_income)) is None else str(r),
        "overdue_count": data.overdue_count,
        "overdue_total": str(data.overdue_total),
        "unreconciled_count": data.unreconciled_count,
        "forecast": [
            {"week": w.week, "week_end": w.week_end.isoformat(), "inflow": str(w.inflow),
             "outflow": str(w.outflow), "closing": str(w.closing), "negative": w.negative}
            for w in data.forecast
        ],
        "alerts": [{"code": a.code, "severity": a.severity, "message": a.message} for a in alerts],
    }
