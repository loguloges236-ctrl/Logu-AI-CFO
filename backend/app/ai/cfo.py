"""Read-only CFO tool registry, call validation and deterministic question routing.

Rules enforced here:
- Only registered tools can run. Each tool has a fixed parameter list.
- Arguments are short strings. Keys that look like SQL, table or write requests are refused.
- No tool name contains a write verb (checked with the Phase 2 guard).
- Questions that ask to move money, post, reverse, delete, change or run SQL are refused before routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.accounting.ai_boundary import assert_read_only


class AIRuleViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: tuple[str, ...] = ()


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("finance.cash_position", "Available cash across cash and bank ledger accounts."),
        ToolSpec("finance.upcoming_payments", "Payments due in a window: today, tomorrow, week, overdue or all.", ("scope",)),
        ToolSpec("finance.receivables", "What customers owe, open and overdue."),
        ToolSpec("finance.payables", "What the business owes suppliers, open and overdue."),
        ToolSpec("finance.debt_summary", "Total debt outstanding, monthly obligation and interest burden."),
        ToolSpec("finance.debt_strategy", "Avalanche and snowball payoff comparison from the monthly budget.", ("monthly_budget",)),
        ToolSpec("finance.period_summary", "Sales, expenses or profit for the current month.", ("metric",)),
        ToolSpec("finance.top_expenses", "Largest expense accounts this month."),
        ToolSpec("finance.cash_movement", "Largest cash outflows this month, to explain a cash change."),
        ToolSpec("finance.risk_alerts", "Deterministic financial risk alerts."),
        ToolSpec("finance.priority_list", "Which payments to make first, given available cash."),
        ToolSpec("bank.reconciliation_status", "Counts of matched, ambiguous and unmatched bank transactions."),
    )
}

FORBIDDEN_ARG_KEYS = frozenset({"sql", "query", "statement", "table", "where", "command", "post", "amount", "account_id"})
_ARG_VALUE_LIMIT = 40


def assert_registry_is_read_only() -> None:
    assert_read_only(frozenset(TOOLS))


def authorize_call(name: str, args: dict[str, str]) -> ToolSpec:
    spec = TOOLS.get(name)
    if spec is None:
        raise AIRuleViolation("unknown_tool", "That tool is not available to the assistant.")
    for key, value in args.items():
        if key in FORBIDDEN_ARG_KEYS or key not in spec.params:
            raise AIRuleViolation("argument_refused", f"Argument {key!r} is not allowed for {name}.")
        if not isinstance(value, str) or len(value) > _ARG_VALUE_LIMIT or not re.fullmatch(r"[A-Za-z0-9_.\- ]*", value):
            raise AIRuleViolation("argument_refused", f"Argument {key!r} has an invalid value.")
    return spec


_REFUSE = re.compile(
    r"\b(transfer|send money|make (a )?payment|pay now|pay (him|her|them|the supplier) now|delete|remove|"
    r"post (the|this|my)|reverse|edit|change my|update|sql|select \*|drop table|insert into|execute)\b",
    re.IGNORECASE,
)

REFUSAL = "I can read and explain your numbers. I cannot move money, make payments, post entries, change stock or run database queries."

_ROUTES: tuple[tuple[re.Pattern[str], str, dict[str, str]], ...] = (
    (re.compile(r"\b(cash|balance)\b.*\b(have|available|position|how much)\b|how much cash", re.I), "finance.cash_position", {}),
    (re.compile(r"\b(this week|due|upcoming|need to pay|payments? do i need)\b", re.I), "finance.upcoming_payments", {"scope": "week"}),
    (re.compile(r"\boverdue\b", re.I), "finance.upcoming_payments", {"scope": "overdue"}),
    (re.compile(r"\b(customers?|receivables?)\b.*\b(owe|owes|outstanding)\b|how much do customers owe", re.I), "finance.receivables", {}),
    (re.compile(r"\b(suppliers?|payables?)\b.*\b(owe|owes|outstanding)\b|how much do i owe", re.I), "finance.payables", {}),
    (re.compile(r"\b(which|what)\b.*\b(debt|loan)\b.*\b(first|pay)\b|avalanche|snowball", re.I), "finance.debt_strategy", {}),
    (re.compile(r"\bbiggest expenses?\b|top expenses?", re.I), "finance.top_expenses", {}),
    (re.compile(r"\b(why)\b.*\bcash\b|cash (reduce|decrease|drop|fall)", re.I), "finance.cash_movement", {}),
    (re.compile(r"\bexpenses?\b.*\b(month|this month)\b|what were my expenses", re.I), "finance.period_summary", {"metric": "expenses"}),
    (re.compile(r"\bhow much did i sell\b|\bsales?\b", re.I), "finance.period_summary", {"metric": "sales"}),
    (re.compile(r"\bprofit\b", re.I), "finance.period_summary", {"metric": "profit"}),
    (re.compile(r"\brisks?\b|\balerts?\b", re.I), "finance.risk_alerts", {}),
    (re.compile(r"\bdebt\b|\bloans?\b|\bemi\b|interest burden", re.I), "finance.debt_summary", {}),
    (re.compile(r"\bfirst\b.*\bpay\b|which payment", re.I), "finance.priority_list", {}),
    (re.compile(r"\breconcil", re.I), "bank.reconciliation_status", {}),
)


@dataclass(frozen=True)
class Route:
    kind: str  # "tool" | "refuse" | "unknown"
    tool: str | None
    args: dict[str, str]
    message: str


def route_question(question: str) -> Route:
    text = question.strip()
    if not text or len(text) > 300:
        return Route("unknown", None, {}, "Ask a short question about your cash, payments, customers, suppliers, debt, sales, expenses or profit.")
    if _REFUSE.search(text):
        return Route("refuse", None, {}, REFUSAL)
    for pattern, tool, args in _ROUTES:
        if pattern.search(text):
            authorize_call(tool, args)
            return Route("tool", tool, dict(args), "")
    return Route("unknown", None, {}, "I can answer questions about cash, payments due, receivables, payables, debt, sales, expenses, profit, risks and reconciliation.")
