"""Controlled executor for the read-only CFO tools.

The executor never builds SQL and never receives a session. It calls one method of a `CFOReader`, and the reader
is a protocol that exposes getters only. Every call first passes `authorize_call`, which refuses unknown tools,
unknown parameters and forbidden keys. Results are converted to JSON-safe values (Decimal becomes a string).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from app.ai.cfo import TOOLS, AIRuleViolation, authorize_call

# tool name -> CFOReader method. Every method takes plain strings and returns plain data.
READER_METHOD = {
    "finance.cash_position": "cash_position",
    "finance.upcoming_payments": "upcoming_payments",
    "finance.receivables": "receivables",
    "finance.payables": "payables",
    "finance.debt_summary": "debt_summary",
    "finance.debt_strategy": "debt_strategy",
    "finance.period_summary": "period_summary",
    "finance.top_expenses": "top_expenses",
    "finance.cash_movement": "cash_movement",
    "finance.risk_alerts": "risk_alerts",
    "finance.priority_list": "priority_list",
    "bank.reconciliation_status": "reconciliation_status",
}

SCOPES = ("today", "tomorrow", "week", "overdue", "all")
METRICS = ("sales", "expenses", "profit")


class CFOReader(Protocol):
    """Read-only surface. It must not gain any method that writes, posts or pays."""

    def cash_position(self) -> Any: ...
    def upcoming_payments(self, scope: str) -> Any: ...
    def receivables(self) -> Any: ...
    def payables(self) -> Any: ...
    def debt_summary(self) -> Any: ...
    def debt_strategy(self, monthly_budget: str) -> Any: ...
    def period_summary(self, metric: str) -> Any: ...
    def top_expenses(self) -> Any: ...
    def cash_movement(self) -> Any: ...
    def risk_alerts(self) -> Any: ...
    def priority_list(self) -> Any: ...
    def reconciliation_status(self) -> Any: ...


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _validated_args(name: str, args: dict[str, str]) -> dict[str, str]:
    spec = TOOLS[name]
    unknown = set(args) - set(spec.params)
    if unknown:
        raise AIRuleViolation("unknown_parameter", f"Parameters not allowed for {name}: {sorted(unknown)}")
    clean: dict[str, str] = {}
    for key in spec.params:
        value = str(args.get(key, "")).strip()
        if key == "scope":
            value = value or "all"
            if value not in SCOPES:
                raise AIRuleViolation("invalid_scope", f"scope must be one of {SCOPES}")
        if key == "metric":
            value = value or "profit"
            if value not in METRICS:
                raise AIRuleViolation("invalid_metric", f"metric must be one of {METRICS}")
        if key == "monthly_budget":
            try:
                budget = Decimal(value)
            except Exception:
                raise AIRuleViolation("invalid_budget", "monthly_budget must be a number") from None
            if budget < 0:
                raise AIRuleViolation("invalid_budget", "monthly_budget cannot be negative")
            value = str(budget)
        clean[key] = value
    return clean


def execute_tool(name: str, args: dict[str, str], reader: CFOReader) -> dict[str, Any]:
    authorize_call(name, args)
    clean = _validated_args(name, args)
    method = getattr(reader, READER_METHOD[name])
    result = method(**clean)
    return {"tool": name, "args": clean, "result": _json_safe(result)}
