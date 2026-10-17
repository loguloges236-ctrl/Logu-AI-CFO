"""Owner authentication: login, opaque server-side sessions, device binding, CSRF and origin checks."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from app.core.clock import ensure_utc, utcnow
from app.core.config import get_settings
from app.core.errors import APIError, unauthorized
from app.db.session import get_db
from app.models import Device, Owner
from app.models import Session as OwnerSession
from app.security.digester import get_digester
from app.security.keys import new_secret
from app.security.passwords import (
    burn_verification_time,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.security.proxy import CLIENT_IP_KEY
from app.services import throttle

LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=60)
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetimes around 400 days


@dataclass
class AuthenticatedSession:
    owner: Owner
    session: OwnerSession


def digest(purpose: str, value: str) -> str:
    return get_digester().digest(purpose, value)


def serialize_owner(owner: Owner) -> dict[str, object]:
    return {
        "id": owner.id,
        "email": owner.email,
        "default_currency": owner.default_currency,
        "voice_unlock_enabled": owner.voice_unlock_enabled,
    }


def _user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:512] if value else None


# devices.display_name is VARCHAR(120); a full browser User-Agent is longer, so the label is truncated.
DEVICE_DISPLAY_NAME_MAX = 120


def _device_display_name(request: Request) -> str | None:
    value = _user_agent(request)
    return value[:DEVICE_DISPLAY_NAME_MAX] if value else None


def _client_ip(request: Request) -> str | None:
    return request.scope.get(CLIENT_IP_KEY)


def verify_origin(request: Request) -> None:
    """Reject state-changing requests whose Origin header is neither configured nor same-origin."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    settings = get_settings()
    if origin in settings.frontend_origins:
        return
    host = request.headers.get("host", "")
    if origin == f"{request.url.scheme}://{host}":
        return
    raise APIError(403, "origin_not_allowed", "Request could not be verified.")


def _cookie_options(max_age: int) -> dict[str, object]:
    settings = get_settings()
    return {
        "max_age": max_age,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }


def set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_minutes * 60
    response.set_cookie(settings.session_cookie_name, session_token, httponly=True, **_cookie_options(max_age))
    # The CSRF cookie must be readable by the page to echo it in a header (double-submit pattern).
    response.set_cookie(settings.csrf_cookie_name, csrf_token, httponly=False, **_cookie_options(max_age))


def set_device_cookie(response: Response, device_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.device_cookie_name, device_token, httponly=True, **_cookie_options(DEVICE_COOKIE_MAX_AGE)
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/")


def login_owner(db: DBSession, request: Request, email: str, password: str) -> Owner | None:
    """Return the owner on success, None on any credential failure. Raises 429 when throttled."""
    scopes = throttle.login_scopes(_client_ip(request), email)
    throttle.ensure_allowed(db, scopes)
    owner = db.scalar(select(Owner).where(Owner.email == email))
    if owner is None:
        burn_verification_time(password)
        matched = False
    else:
        matched = verify_password(owner.password_hash, password) and owner.is_active
    if not matched or owner is None:
        throttle.record_failure(db, scopes)
        return None
    if password_needs_rehash(owner.password_hash):
        owner.password_hash = hash_password(password)
    return owner


def resolve_device(db: DBSession, owner: Owner, request: Request) -> tuple[Device, str | None]:
    """Return (device, new_cookie_value). new_cookie_value is set only when a device row was created."""
    settings = get_settings()
    now = utcnow()
    raw = request.cookies.get(settings.device_cookie_name)
    if raw:
        device = db.scalar(
            select(Device).where(
                Device.identifier_hash == digest("device", raw),
                Device.owner_id == owner.id,
                Device.revoked_at.is_(None),
            )
        )
        if device is not None:
            device.last_seen_at = now
            return device, None
    raw = new_secret(32)
    device = Device(
        owner_id=owner.id,
        identifier_hash=digest("device", raw),
        display_name=_device_display_name(request),
        user_agent=_user_agent(request),
        last_seen_at=now,
    )
    db.add(device)
    db.flush()
    return device, raw


def create_session(
    db: DBSession, owner: Owner, request: Request, device: Device | None
) -> tuple[OwnerSession, str, str]:
    settings = get_settings()
    now = utcnow()
    raw_token = new_secret(48)
    csrf_token = new_secret(32)
    client_ip = _client_ip(request)
    session = OwnerSession(
        owner_id=owner.id,
        device_id=device.id if device else None,
        token_hash=digest("session", raw_token),
        csrf_token_hash=digest("csrf", csrf_token),
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
        last_seen_at=now,
        ip_hash=digest("ip", client_ip) if client_ip else None,
        user_agent=_user_agent(request),
    )
    db.add(session)
    db.flush()
    return session, raw_token, csrf_token


def _revoke(session: OwnerSession, reason: str, now=None) -> None:
    session.revoked_at = now or utcnow()
    session.revoke_reason = reason


def revoke_sessions(
    db: DBSession, owner_id, reason: str, *, keep_session_id=None
) -> int:
    """Revoke every active session of the owner, optionally keeping one. Returns the number revoked."""
    now = utcnow()
    statement = (
        update(OwnerSession)
        .where(OwnerSession.owner_id == owner_id, OwnerSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )
    if keep_session_id is not None:
        statement = statement.where(OwnerSession.id != keep_session_id)
    result = db.execute(statement)
    return int(result.rowcount or 0)


def get_current_auth(request: Request, db: DBSession = Depends(get_db)) -> AuthenticatedSession:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise unauthorized()
    session = db.scalar(select(OwnerSession).where(OwnerSession.token_hash == digest("session", raw)))
    if session is None or session.revoked_at is not None:
        raise unauthorized()
    now = utcnow()
    if ensure_utc(session.expires_at) <= now:
        _revoke(session, "expired", now)
        db.commit()
        raise unauthorized()
    idle_limit = ensure_utc(session.last_seen_at) + timedelta(minutes=settings.session_idle_minutes)
    if idle_limit <= now:
        _revoke(session, "idle_timeout", now)
        db.commit()
        raise unauthorized()
    owner = db.get(Owner, session.owner_id)
    if owner is None or not owner.is_active:
        raise unauthorized()
    if session.device_id is not None:
        device = db.get(Device, session.device_id)
        if device is None or device.revoked_at is not None:
            _revoke(session, "device_revoked", now)
            db.commit()
            raise unauthorized()
    if now - ensure_utc(session.last_seen_at) >= LAST_SEEN_WRITE_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return AuthenticatedSession(owner=owner, session=session)


def require_csrf(
    request: Request, auth: AuthenticatedSession = Depends(get_current_auth)
) -> AuthenticatedSession:
    verify_origin(request)
    settings = get_settings()
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not header or not cookie or not hmac.compare_digest(header.encode(), cookie.encode()):
        raise _csrf_failed()
    if not hmac.compare_digest(digest("csrf", header), auth.session.csrf_token_hash):
        raise _csrf_failed()
    return auth


def _csrf_failed() -> APIError:
    return APIError(403, "csrf_validation_failed", "Request could not be verified.")
