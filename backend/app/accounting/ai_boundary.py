"""Controlled boundary for future AI features.

The language model never receives database access, SQL, or posting rights. A future AI feature may
call only the read-only tools named here. Every write (creating, posting, reversing, closing) stays
in application services that validate the rules in `domain.py`.
"""

from __future__ import annotations

READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "accounting.list_accounts",
        "accounting.get_account_ledger",
        "accounting.get_trial_balance",
        "accounting.get_profit_and_loss",
        "accounting.get_balance_sheet",
        "accounting.get_cash_position",
    }
)

_WRITE_VERBS = ("create", "post", "reverse", "close", "update", "delete", "set", "insert", "sql", "query")


def assert_read_only(tool_names: frozenset[str]) -> None:
    """Refuse any tool whose name suggests a write or raw query. Used by tests and at registration."""
    for name in tool_names:
        leaf = name.rsplit(".", 1)[-1].lower()
        if any(verb in leaf for verb in _WRITE_VERBS):
            raise ValueError(f"AI tool {name!r} is not read-only")
