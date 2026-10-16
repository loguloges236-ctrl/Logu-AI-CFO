"""Reminder engine wired to real obligations, documents and risk figures.

Channels: only the in-app channel is configured. It stores rows in `notifications`, and the unique `dedupe_key` makes a
repeated run a no-op. External email/SMS/push providers are not configured, so nothing claims external delivery.
Customer collections are reminded only once overdue, because the notification kinds have no "due soon" kind for them.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DBSession

from app.finance.domain import FinanceRuleViolation
from app.models.commerce import DocumentProcessingJob
from app.models.finance import Notification, ReconciliationSuggestion
from app.notifications.domain import DuePayment, InAppChannel, Message, NotificationChannel, dedupe_key, plan_reminders
from app.notifications.scheduler import CycleReport, run_reminder_cycle
from app.services import finance as fin
from app.services.auth import AuthenticatedSession
from app.services.commerce_common import audit, unit_of_work


def _store(db: DBSession, message: Message) -> bool:
    """Inserts one notification. Returns False when the same dedupe_key already exists."""
    statement = (
        pg_insert(Notification)
        .values(kind=message.kind, title=message.title[:160], body=message.body[:500],
                dedupe_key=message.dedupe_key[:200], due_date=message.due_date, channel="in_app")
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Notification.id)
    )
    return db.execute(statement).scalar() is not None


def payment_reminders(db: DBSession, today: date) -> list[DuePayment]:
    items: list[DuePayment] = []
    for row in fin._open_due_rows(db, today):
        items.append(DuePayment(row.obligation_id, row.kind, row.title, row.due, row.amount, paid=False))
    for entry in fin._open_documents(db, fin.PAYABLE):
        if entry["_due"] is not None:
            items.append(DuePayment(entry["id"], "supplier_settlement", f"Pay {entry['party']} {entry['number']}".strip(),
                                    entry["_due"], entry["_outstanding"], paid=False))
    for entry in fin._open_documents(db, fin.RECEIVABLE):
        if entry["_due"] is not None and entry["_due"] < today:
            items.append(DuePayment(entry["id"], fin.RECEIVABLE, f"Collect from {entry['party']} {entry['number']}".strip(),
                                    entry["_due"], entry["_outstanding"], paid=False))
    return items


def review_messages(db: DBSession, today: date) -> list[Message]:
    out: list[Message] = []
    jobs = db.scalars(select(DocumentProcessingJob).where(DocumentProcessingJob.status == "needs_review"))
    for job in jobs:
        out.append(Message("document_review_required", "Document needs your review",
                           "An uploaded document has values to check before it can be approved.",
                           dedupe_key("document_review_required", str(job.id), today), today))
    pending = db.scalar(select(ReconciliationSuggestion.id).where(ReconciliationSuggestion.status == "suggested").limit(1))
    if pending is not None:
        out.append(Message("reconciliation_issue", "Bank reconciliation needs review",
                           "Some bank transactions have suggested matches waiting for your decision.",
                           dedupe_key("reconciliation_issue", "pending", today), today))
    return out


def risk_messages(alerts: list[dict[str, Any]], today: date) -> list[Message]:
    return [
        Message("financial_risk_alert", f"Risk: {a['code'].replace('_', ' ')}", a["message"],
                dedupe_key("financial_risk_alert", a["code"], today), today)
        for a in alerts if a.get("severity") == "critical"
    ]


def run_reminders(db: DBSession, request: Request, auth: AuthenticatedSession, today: date) -> dict[str, Any]:
    with unit_of_work(db):
        in_app: NotificationChannel = InAppChannel(lambda message: _store(db, message))
        payments = run_reminder_cycle(payment_reminders(db, today), today, in_app)
        extra_report = CycleReport()
        for message in review_messages(db, today) + risk_messages(_alerts(db, today), today):
            extra_report.planned += 1
            extra_report.kinds[message.kind] = extra_report.kinds.get(message.kind, 0) + 1
            if in_app.deliver(message):
                extra_report.delivered += 1
            else:
                extra_report.not_delivered += 1
        audit(db, request, auth, "reminders.run", "notifications", today.isoformat(), {
            "planned": payments.planned + extra_report.planned,
            "stored": payments.delivered + extra_report.delivered,
            "already_stored": payments.not_delivered + extra_report.not_delivered,
            "external_channels": 0,
        })
    kinds: dict[str, int] = dict(payments.kinds)
    for kind, count in extra_report.kinds.items():
        kinds[kind] = kinds.get(kind, 0) + count
    return {"planned": payments.planned + extra_report.planned,
            "stored": payments.delivered + extra_report.delivered,
            "already_stored": payments.not_delivered + extra_report.not_delivered,
            "kinds": kinds, "external_delivery": "not configured"}


def _alerts(db: DBSession, today: date) -> list[dict[str, Any]]:
    return fin.dashboard(db, today)["alerts"]


def list_notifications(db: DBSession, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    query = select(Notification).order_by(Notification.created_at.desc()).limit(min(limit, 200))
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return [{"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
             "due_date": n.due_date.isoformat() if n.due_date else None, "read": n.read_at is not None}
            for n in db.scalars(query)]


def mark_read(db: DBSession, notification_id: UUID) -> None:
    """Only read_at can change (database trigger). Read state is not a financial mutation, so no audit event."""
    from datetime import datetime, timezone

    with unit_of_work(db):
        result = db.execute(update(Notification).where(Notification.id == notification_id)
                            .values(read_at=datetime.now(timezone.utc)))
        if result.rowcount == 0:
            raise FinanceRuleViolation("not_found", "Notification was not found")
