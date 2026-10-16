"""Owner account operations: password change, password reset, session and device management."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from app.core.clock import ensure_utc, utcnow
from app.core.config import get_settings
from app.core.errors import APIError
from app.models import Device, Owner, PasswordResetToken
from app.models import Session as OwnerSession
from app.security.keys import new_secret
from app.security.passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    burn_verification_time,
    hash_password,
    verify_password,
)
from app.security.proxy import CLIENT_IP_KEY
from app.services import throttle
from app.services.auth import digest, revoke_sessions
from app.services.interfaces import NotificationProvider

logger = logging.getLogger(__name__)


def _validate_new_password(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise APIError(
            422,
            "weak_password",
            f"Password must be between {PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters.",
        )


def change_password(
    db: DBSession,
    request: Request,
    owner: Owner,
    current_session_id: UUID,
    current_password: str,
    new_password: str,
) -> int:
    """Change the password, keep the current session, revoke every other session. Returns revoked count."""
    scopes = throttle.password_change_scopes(str(owner.id))
    throttle.ensure_allowed(db, scopes)
    if not verify_password(owner.password_hash, current_password):
        throttle.record_failure(db, scopes)
        raise APIError(401, "invalid_current_password", "Current password is incorrect.")
    _validate_new_password(new_password)
    if verify_password(owner.password_hash, new_password):
        raise APIError(422, "password_unchanged", "Choose a password different from the current one.")
    owner.password_hash = hash_password(new_password)
    owner.password_changed_at = utcnow()
    return revoke_sessions(db, owner.id, "password_changed", keep_session_id=current_session_id)


def request_password_reset(
    db: DBSession, request: Request, email: str, notifier: NotificationProvider
) -> None:
    """Always returns normally so the response cannot reveal whether the email exists.

    A reset token is issued and delivered only when a notification provider is available. Until one
    is integrated, owner recovery is performed on the server with `python -m app.cli reset-owner-password`.
    """
    settings = get_settings()
    scopes = throttle.reset_request_scopes(request.scope.get(CLIENT_IP_KEY), email)
    if not _is_throttled(db, scopes):
        throttle.record_failure(db, scopes)
    owner = db.scalar(select(Owner).where(Owner.email == email))
    if owner is None or not owner.is_active or not notifier.available:
        burn_verification_time(email)
        return
    token = new_secret(32)
    now = utcnow()
    db.add(
        PasswordResetToken(
            owner_id=owner.id,
            token_hash=digest("password-reset", token),
            expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
            created_at=now,
        )
    )
    db.flush()
    try:
        notifier.send(
            channel="email",
            recipient=owner.email,
            title="Logu AI CFO password reset",
            body=f"Use this one-time reset code within {settings.password_reset_ttl_minutes} minutes: {token}",
        )
    except Exception:  # never log the token or the recipient
        logger.warning("Password reset notification could not be delivered.")


def _is_throttled(db: DBSession, scopes: list[throttle.Scope]) -> bool:
    try:
        throttle.ensure_allowed(db, scopes)
    except APIError:
        return True
    return False


def confirm_password_reset(
    db: DBSession, request: Request, token: str, new_password: str
) -> None:
    scopes = throttle.reset_confirm_scopes(request.scope.get(CLIENT_IP_KEY))
    throttle.ensure_allowed(db, scopes)
    invalid = APIError(400, "invalid_reset_token", "The reset code is invalid or has expired.")
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == digest("password-reset", token))
    )
    now = utcnow()
    owner = db.get(Owner, row.owner_id) if row is not None else None
    if row is None or row.used_at is not None or ensure_utc(row.expires_at) <= now:
        throttle.record_failure(db, scopes)
        raise invalid
    if owner is None or not owner.is_active:
        throttle.record_failure(db, scopes)
        raise invalid
    _validate_new_password(new_password)
    owner.password_hash = hash_password(new_password)
    owner.password_changed_at = now
    row.used_at = now
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.owner_id == owner.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    revoke_sessions(db, owner.id, "password_reset")


def list_sessions(db: DBSession, owner_id: UUID) -> list[OwnerSession]:
    now = utcnow()
    return list(
        db.scalars(
            select(OwnerSession)
            .where(
                OwnerSession.owner_id == owner_id,
                OwnerSession.revoked_at.is_(None),
                OwnerSession.expires_at > now,
            )
            .order_by(OwnerSession.last_seen_at.desc())
        )
    )


def revoke_session(db: DBSession, owner_id: UUID, session_id: UUID, reason: str) -> bool:
    session = db.scalar(
        select(OwnerSession).where(OwnerSession.id == session_id, OwnerSession.owner_id == owner_id)
    )
    if session is None or session.revoked_at is not None:
        return False
    session.revoked_at = utcnow()
    session.revoke_reason = reason
    return True


def list_devices(db: DBSession, owner_id: UUID) -> list[Device]:
    return list(
        db.scalars(
            select(Device).where(Device.owner_id == owner_id).order_by(Device.last_seen_at.desc())
        )
    )


def revoke_device(db: DBSession, owner_id: UUID, device_id: UUID) -> bool:
    device = db.scalar(select(Device).where(Device.id == device_id, Device.owner_id == owner_id))
    if device is None:
        return False
    now = utcnow()
    if device.revoked_at is None:
        device.revoked_at = now
    db.execute(
        update(OwnerSession)
        .where(
            OwnerSession.device_id == device.id,
            OwnerSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="device_revoked")
    )
    return True
