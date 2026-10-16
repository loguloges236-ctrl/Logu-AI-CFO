"""Reminder cycle: plan the messages for a day and hand them to a channel.

The scheduler does not decide delivery itself. A channel returns True when it accepted a message. The in-app channel
returns False for a dedupe_key it already stored, so running the cycle twice on the same day stores nothing new.
Nothing is sent to an external provider unless one is configured behind a NotificationChannel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.notifications.domain import DuePayment, Message, NotificationChannel, plan_reminders


@dataclass
class CycleReport:
    planned: int = 0
    delivered: int = 0
    not_delivered: int = 0
    kinds: dict[str, int] = field(default_factory=dict)


def run_reminder_cycle(payments: list[DuePayment], today: date, channel: NotificationChannel) -> CycleReport:
    report = CycleReport()
    messages: list[Message] = plan_reminders(payments, today)
    report.planned = len(messages)
    for message in messages:
        report.kinds[message.kind] = report.kinds.get(message.kind, 0) + 1
        if channel.deliver(message):
            report.delivered += 1
        else:
            report.not_delivered += 1
    return report
