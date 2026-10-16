"""Reminder planning and notification abstraction. Pure rules; the service persists what this module plans."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

KINDS = (
    "emi_due",
    "debt_payment_due",
    "supplier_payment_due",
    "customer_payment_overdue",
    "salary_date",
    "recurring_expense",
    "document_review_required",
    "reconciliation_issue",
    "financial_risk_alert",
)
LEAD_DAYS = (0, 1, 3, 7)

_KIND_FOR_OBLIGATION = {
    "emi": "emi_due",
    "interest": "debt_payment_due",
    "supplier_settlement": "supplier_payment_due",
    "salary": "salary_date",
}


@dataclass(frozen=True)
class Message:
    kind: str
    title: str
    body: str
    dedupe_key: str
    due_date: date | None


@dataclass(frozen=True)
class DuePayment:
    obligation_id: str
    kind: str
    title: str
    due: date
    amount: Decimal
    paid: bool


def dedupe_key(kind: str, reference: str, period: date) -> str:
    return f"{kind}:{reference}:{period.isoformat()}"


def plan_reminders(payments: list[DuePayment], today: date) -> list[Message]:
    """One message per unpaid occurrence due within the lead window or already overdue. Keys are stable, so
    running the planner twice on the same day never duplicates a notification."""
    messages: list[Message] = []
    for item in payments:
        if item.paid:
            continue
        days = (item.due - today).days
        if days > max(LEAD_DAYS):
            continue
        kind = _KIND_FOR_OBLIGATION.get(item.kind, "recurring_expense")
        if days < 0:
            title = f"Overdue: {item.title}"
            body = f"{item.title} of {item.amount} was due on {item.due.isoformat()} and is not paid."
            kind = "customer_payment_overdue" if item.kind == "receivable" else kind
        elif days == 0:
            title = f"Due today: {item.title}"
            body = f"{item.title} of {item.amount} is due today."
        else:
            title = f"Due in {days} day(s): {item.title}"
            body = f"{item.title} of {item.amount} is due on {item.due.isoformat()}."
        messages.append(Message(kind, title, body, dedupe_key(kind, item.obligation_id, item.due), item.due))
    return messages


class NotificationChannel(ABC):
    """A delivery channel. Email, push and SMS providers implement this; none is bundled."""

    name: str

    @abstractmethod
    def deliver(self, message: Message) -> bool:
        """Return True when the message was accepted for delivery."""


class InAppChannel(NotificationChannel):
    name = "in_app"

    def __init__(self, persist: Callable[[Message], bool]) -> None:
        self._persist = persist

    def deliver(self, message: Message) -> bool:
        return self._persist(message)


class NoExternalChannel(NotificationChannel):
    """Placeholder for email, push and SMS. Reports that nothing was delivered."""

    name = "external_unconfigured"

    def deliver(self, message: Message) -> bool:
        return False
