"""Pure finance rules. Every number the owner sees is computed here from application data.

Money is Decimal with ROUND_HALF_UP at 2 places. Rates are percentages per year. Nothing in this module reads
the database, calls a provider, or asks a language model for a figure.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MAX_MONTHS = 600
MAX_MONEY = Decimal("9999999999999999.99")
_MONEY_RE = re.compile(r"^\d{1,16}(\.\d{1,2})?$")
_RATE_RE = re.compile(r"^\d{1,3}(\.\d{1,4})?$")

INTEREST_TYPES = ("reducing", "flat")
FREQUENCIES = ("once", "weekly", "monthly", "yearly")
OBLIGATION_KINDS = (
    "rent", "emi", "insurance", "subscription", "supplier_settlement", "credit_card",
    "utilities", "salary", "interest", "other",
)
CRITICAL_KINDS = frozenset({"rent", "emi", "salary", "utilities", "insurance", "interest"})
STRATEGIES = ("avalanche", "snowball")
BUCKETS = ("overdue", "today", "tomorrow", "this_week", "later")


class FinanceRuleViolation(ValueError):
    """A rule failed. `code` is stable and safe to show to the owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> FinanceRuleViolation:
    return FinanceRuleViolation(code, message)


# ---------------------------------------------------------------- money and rates


def parse_money(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    """Money arrives as a decimal string. Floats and numbers are refused so binary rounding never enters."""
    if not isinstance(value, str) or not _MONEY_RE.fullmatch(value):
        raise _fail("invalid_money", f"{field} must be a decimal string with at most 2 decimal places")
    amount = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount > MAX_MONEY:
        raise _fail("invalid_money", f"{field} is too large")
    if amount < 0 or (amount == 0 and not allow_zero):
        raise _fail("invalid_money", f"{field} must be greater than zero" if not allow_zero else f"{field} cannot be negative")
    return amount


def parse_rate(value: object, field: str) -> Decimal:
    """Annual percentage rate, 0 to 100, at most 4 decimal places, given as a string."""
    if value is None or value == "":
        return Decimal("0")
    if not isinstance(value, str) or not _RATE_RE.fullmatch(value):
        raise _fail("invalid_rate", f"{field} must be a percentage string such as 8.5")
    rate = Decimal(value)
    if rate > 100:
        raise _fail("invalid_rate", f"{field} cannot exceed 100")
    return rate


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def add_months(start: date, months: int) -> date:
    """Calendar month arithmetic that clamps the day (31 Jan + 1 month = 28/29 Feb)."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------- EMI and interest schedule


@dataclass(frozen=True)
class ScheduleRow:
    installment_no: int
    due_date: date
    opening: Decimal
    interest: Decimal
    principal: Decimal
    emi: Decimal
    closing: Decimal


def _check_term(months: int) -> None:
    if not 1 <= months <= MAX_MONTHS:
        raise _fail("invalid_tenure", f"Tenure must be between 1 and {MAX_MONTHS} months")


def emi_amount(principal: Decimal, annual_rate_pct: Decimal, months: int, interest_type: str) -> Decimal:
    """Reducing balance: P·r·(1+r)^n / ((1+r)^n − 1). Flat: (P + P·rate·years) / n."""
    _check_term(months)
    if interest_type not in INTEREST_TYPES:
        raise _fail("invalid_interest_type", "Interest type must be reducing or flat")
    if interest_type == "flat":
        total_interest = principal * annual_rate_pct / Decimal(100) * Decimal(months) / Decimal(12)
        return money((principal + total_interest) / Decimal(months))
    monthly_rate = annual_rate_pct / Decimal(100) / Decimal(12)
    if monthly_rate == 0:
        return money(principal / Decimal(months))
    factor = (1 + monthly_rate) ** months
    return money(principal * monthly_rate * factor / (factor - 1))


def build_schedule(
    principal: Decimal, annual_rate_pct: Decimal, months: int, interest_type: str, start: date
) -> list[ScheduleRow]:
    """Installment-by-installment split of each EMI into interest and principal. The last row clears the balance
    exactly, so the schedule always ends at zero and principal sums to the loan amount."""
    if principal <= 0:
        raise _fail("invalid_money", "Principal must be greater than zero")
    emi = emi_amount(principal, annual_rate_pct, months, interest_type)
    rows: list[ScheduleRow] = []
    balance = principal

    if interest_type == "flat":
        total_interest = money(principal * annual_rate_pct / Decimal(100) * Decimal(months) / Decimal(12))
        per_month = money(total_interest / Decimal(months))
        interest_paid = Decimal("0")
        for n in range(1, months + 1):
            interest = per_month if n < months else total_interest - interest_paid
            interest_paid += interest
            principal_part = balance if n == months else emi - interest
            closing = balance - principal_part
            rows.append(ScheduleRow(n, add_months(start, n), balance, interest, principal_part, interest + principal_part, closing))
            balance = closing
        return rows

    monthly_rate = annual_rate_pct / Decimal(100) / Decimal(12)
    for n in range(1, months + 1):
        interest = money(balance * monthly_rate)
        principal_part = balance if n == months else min(emi - interest, balance)
        closing = balance - principal_part
        rows.append(ScheduleRow(n, add_months(start, n), balance, interest, principal_part, interest + principal_part, closing))
        balance = closing
    return rows


def total_interest(rows: list[ScheduleRow]) -> Decimal:
    return sum((row.interest for row in rows), Decimal("0"))


# ---------------------------------------------------------------- recurrence


def occurrences(anchor: date, frequency: str, window_start: date, window_end: date, end: date | None = None) -> list[date]:
    """Due dates of a recurring obligation that fall inside [window_start, window_end]."""
    if frequency not in FREQUENCIES:
        raise _fail("invalid_frequency", "Frequency must be once, weekly, monthly or yearly")
    if frequency == "once":
        candidates = [anchor]
    else:
        candidates = []
        for i in range(0, 10_000):
            if frequency == "weekly":
                due = anchor + timedelta(days=7 * i)
            elif frequency == "monthly":
                due = add_months(anchor, i)
            else:
                due = add_months(anchor, 12 * i)
            if due > window_end or (end is not None and due > end):
                break
            candidates.append(due)
    return [d for d in candidates if window_start <= d <= window_end and (end is None or d <= end)]


def bucket(due: date, today: date) -> str:
    days = (due - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days <= 6:
        return "this_week"
    return "later"


# ---------------------------------------------------------------- payment priority


@dataclass(frozen=True)
class PayItem:
    key: str
    amount: Decimal
    due: date
    annual_rate_pct: Decimal
    kind: str


@dataclass(frozen=True)
class PriorityRow:
    key: str
    rank: int
    score: int
    bucket: str
    cumulative: Decimal
    affordable: bool
    reasons: tuple[str, ...]


def prioritise(items: list[PayItem], available_cash: Decimal, today: date) -> list[PriorityRow]:
    """Ranks what to pay first. Overdue items outrank everything; then nearer due dates; then interest cost; then
    business criticality. `affordable` is true while the running total still fits the available cash."""
    scored: list[tuple[int, date, str, PayItem, tuple[str, ...]]] = []
    for item in items:
        days = (item.due - today).days
        reasons: list[str] = []
        score = 0
        if days < 0:
            score += 1000 + min(-days, 365)
            reasons.append(f"overdue by {-days} day(s)")
        else:
            score += max(0, 60 - days) * 5
            reasons.append("due today" if days == 0 else f"due in {days} day(s)")
        if item.annual_rate_pct > 0:
            score += int(min(item.annual_rate_pct, Decimal(100)) * 2)
            reasons.append(f"interest {item.annual_rate_pct}% a year")
        if item.kind in CRITICAL_KINDS:
            score += 200
            reasons.append("business critical")
        scored.append((score, item.due, item.key, item, tuple(reasons)))

    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    rows: list[PriorityRow] = []
    running = Decimal("0")
    for rank, (score, due, key, item, reasons) in enumerate(scored, start=1):
        running += item.amount
        rows.append(
            PriorityRow(
                key=key,
                rank=rank,
                score=score,
                bucket=bucket(due, today),
                cumulative=running,
                affordable=running <= available_cash,
                reasons=reasons,
            )
        )
    return rows


# ---------------------------------------------------------------- debt strategies


@dataclass(frozen=True)
class DebtPlan:
    key: str
    balance: Decimal
    annual_rate_pct: Decimal
    minimum: Decimal


@dataclass(frozen=True)
class PayoffResult:
    strategy: str
    months: int | None
    total_interest: Decimal
    total_paid: Decimal
    payoff_order: tuple[str, ...]


def simulate_payoff(plans: list[DebtPlan], monthly_budget: Decimal, strategy: str) -> PayoffResult:
    """Month-by-month payoff. Each month: interest accrues, every debt receives its minimum, and the remaining
    budget goes to one target. Avalanche targets the highest rate; snowball targets the smallest balance.
    `months` is None when the debts are not cleared within MAX_MONTHS."""
    if strategy not in STRATEGIES:
        raise _fail("invalid_strategy", "Strategy must be avalanche or snowball")
    if not plans:
        return PayoffResult(strategy, 0, Decimal("0"), Decimal("0"), ())
    minimums = sum((p.minimum for p in plans), Decimal("0"))
    if monthly_budget < minimums:
        raise _fail("budget_below_minimums", "The monthly budget does not cover the minimum payments")

    balances = {p.key: p.balance for p in plans}
    rates = {p.key: p.annual_rate_pct for p in plans}
    mins = {p.key: p.minimum for p in plans}
    order_done: list[str] = []
    interest_total = Decimal("0")
    paid_total = Decimal("0")

    for month in range(1, MAX_MONTHS + 1):
        open_keys = [k for k in balances if balances[k] > 0]
        if not open_keys:
            return PayoffResult(strategy, month - 1, interest_total, paid_total, tuple(order_done))
        for k in open_keys:
            accrued = money(balances[k] * rates[k] / Decimal(100) / Decimal(12))
            balances[k] += accrued
            interest_total += accrued
        remaining = monthly_budget
        for k in open_keys:
            pay = min(mins[k], balances[k])
            balances[k] -= pay
            paid_total += pay
            remaining -= pay
        if strategy == "avalanche":
            targets = sorted((k for k in balances if balances[k] > 0), key=lambda k: (-rates[k], balances[k], k))
        else:
            targets = sorted((k for k in balances if balances[k] > 0), key=lambda k: (balances[k], -rates[k], k))
        for k in targets:
            if remaining <= 0:
                break
            pay = min(balances[k], remaining)
            balances[k] -= pay
            paid_total += pay
            remaining -= pay
        for k in balances:
            if balances[k] == 0 and k not in order_done:
                order_done.append(k)

    return PayoffResult(strategy, None, interest_total, paid_total, tuple(order_done))


def compare_strategies(plans: list[DebtPlan], monthly_budget: Decimal) -> dict[str, object]:
    avalanche = simulate_payoff(plans, monthly_budget, "avalanche")
    snowball = simulate_payoff(plans, monthly_budget, "snowball")
    saved = None
    if avalanche.months is not None and snowball.months is not None:
        saved = money(snowball.total_interest - avalanche.total_interest)
    return {
        "avalanche": avalanche,
        "snowball": snowball,
        "interest_saved_by_avalanche": saved,
    }


# ---------------------------------------------------------------- cash forecast and alerts


@dataclass(frozen=True)
class DatedAmount:
    due: date
    amount: Decimal
    label: str


@dataclass(frozen=True)
class ForecastWeek:
    week: int
    week_end: date
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    negative: bool


def forecast(opening: Decimal, inflows: list[DatedAmount], outflows: list[DatedAmount], today: date, weeks: int) -> list[ForecastWeek]:
    """Week-by-week cash projection. Items already overdue fall into week 1, because they are still owed."""
    if not 1 <= weeks <= 26:
        raise _fail("invalid_horizon", "Forecast horizon must be between 1 and 26 weeks")
    result: list[ForecastWeek] = []
    closing = opening
    for w in range(1, weeks + 1):
        week_start = today + timedelta(days=7 * (w - 1))
        week_end = today + timedelta(days=7 * w - 1)
        inflow = _sum_in_week(inflows, week_start, week_end, first_week=(w == 1), today=today)
        outflow = _sum_in_week(outflows, week_start, week_end, first_week=(w == 1), today=today)
        closing = closing + inflow - outflow
        result.append(ForecastWeek(w, week_end, inflow, outflow, closing, closing < 0))
    return result


def _sum_in_week(items: list[DatedAmount], start: date, end: date, *, first_week: bool, today: date) -> Decimal:
    total = Decimal("0")
    for item in items:
        if (first_week and item.due < today) or (start <= item.due <= end):
            total += item.amount
    return money(total)


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str


def debt_burden(monthly_obligations: Decimal, monthly_income: Decimal) -> Decimal | None:
    """Share of monthly income committed to obligations. None when no income is recorded."""
    if monthly_income <= 0:
        return None
    return (monthly_obligations / monthly_income).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def risk_alerts(
    *,
    cash_available: Decimal,
    overdue_count: int,
    overdue_total: Decimal,
    next_seven_days_outflow: Decimal,
    monthly_income: Decimal,
    monthly_obligations: Decimal,
    receivables_overdue_total: Decimal,
    forecast_negative_week: int | None,
    unreconciled_count: int,
) -> list[Alert]:
    alerts: list[Alert] = []
    if cash_available <= 0:
        alerts.append(Alert("no_cash", "critical", "Available cash is zero or negative."))
    if next_seven_days_outflow > cash_available:
        alerts.append(
            Alert("cash_short_next_7_days", "critical",
                  f"Payments due in the next 7 days ({next_seven_days_outflow}) exceed available cash ({cash_available}).")
        )
    if forecast_negative_week is not None:
        alerts.append(Alert("forecast_negative", "critical", f"Projected cash turns negative in week {forecast_negative_week}."))
    if overdue_count:
        alerts.append(Alert("overdue_payments", "high", f"{overdue_count} payment(s) are overdue, totalling {overdue_total}."))
    burden = debt_burden(monthly_obligations, monthly_income)
    if burden is None and monthly_obligations > 0:
        alerts.append(Alert("no_income_recorded", "warning", "Obligations exist but no monthly income is recorded."))
    elif burden is not None and burden > Decimal("0.40"):
        alerts.append(Alert("debt_burden_high", "warning", f"Obligations take {burden * 100:.1f}% of monthly income (limit 40%)."))
    if receivables_overdue_total > 0:
        alerts.append(Alert("receivables_overdue", "warning", f"Customers owe {receivables_overdue_total} past the due date."))
    if unreconciled_count:
        alerts.append(Alert("bank_reconciliation_pending", "info", f"{unreconciled_count} bank transaction(s) need reconciliation."))
    return alerts
