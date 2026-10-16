"""Audit and security-event recording.

Rows are append-only at the database level (triggers in migration 20261007_0001).
Metadata is filtered: secret-like keys are dropped and only short scalar values are kept.
IP addresses are stored only as keyed HMAC digests.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, SecurityEvent
from app.security.digester import get_digester
from app.security.edge import REQUEST_ID_KEY
from app.security.proxy import CLIENT_IP_KEY

SENSITIVE_METADATA_KEYS = frozenset(
    {"password", "token", "authorization", "cookie", "csrf", "secret", "api_key", "key", "signature"}
)
MAX_METADATA_STRING = 255


def safe_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        lowered = str(key).lower()
        if any(secret in lowered for secret in SENSITIVE_METADATA_KEYS):
            continue
        if isinstance(value, bool) or value is None or isinstance(value, int):
            cleaned[str(key)] = value
        elif isinstance(value, str):
            cleaned[str(key)] = value[:MAX_METADATA_STRING]
    return cleaned


def ip_digest(request: Request) -> str | None:
    ip = request.scope.get(CLIENT_IP_KEY)
    return get_digester().digest("ip", ip) if ip else None


def _request_id(request: Request) -> str | None:
    value = request.scope.get(REQUEST_ID_KEY)
    return str(value)[:64] if value else None


def write_audit(
    db: Session,
    request: Request,
    *,
    action: str,
    success: bool,
    owner_id: UUID | None = None,
    session_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            owner_id=owner_id,
            session_id=session_id,
            action=action[:100],
            target_type=target_type,
            target_id=target_id,
            success=success,
            request_id=_request_id(request),
            metadata_json=safe_metadata(metadata),
        )
    )


def write_security_event(
    db: Session,
    request: Request,
    *,
    event_type: str,
    success: bool,
    owner_id: UUID | None = None,
    session_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        SecurityEvent(
            owner_id=owner_id,
            session_id=session_id,
            event_type=event_type[:100],
            success=success,
            ip_hash=ip_digest(request),
            metadata_json=safe_metadata(metadata),
        )
    )
