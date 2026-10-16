from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_notifier
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.session import get_db
from app.models import Owner
from app.schemas.auth import (
    BootstrapCredentials,
    DeviceItem,
    DeviceListResponse,
    LoginCredentials,
    MessageResponse,
    OwnerResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    ResetConfirmRequest,
    ResetRequest,
    SessionItem,
    SessionListResponse,
    SessionResponse,
)
from app.security.passwords import hash_password
from app.services import accounts
from app.services.audit import write_audit, write_security_event
from app.services.auth import (
    AuthenticatedSession,
    clear_auth_cookies,
    create_session,
    get_current_auth,
    login_owner,
    require_csrf,
    resolve_device,
    revoke_sessions,
    serialize_owner,
    set_device_cookie,
    set_session_cookies,
    verify_origin,
)
from app.services.interfaces import NotificationProvider

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/bootstrap", response_model=OwnerResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_owner(
    credentials: BootstrapCredentials,
    request: Request,
    setup_token: str = Header(default="", alias="X-Setup-Token"),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings()
    if not settings.enable_bootstrap or not settings.bootstrap_token:
        raise APIError(404, "not_found", "Not found.")
    if not hmac.compare_digest(setup_token.encode(), settings.bootstrap_token.encode()):
        raise APIError(403, "setup_not_authorized", "Setup authorization failed.")
    if db.scalar(select(func.count()).select_from(Owner)):
        raise APIError(409, "owner_exists", "The owner has already been created.")
    owner = Owner(
        singleton_guard=True,
        email=credentials.email,
        password_hash=hash_password(credentials.password),
        password_changed_at=utcnow(),
    )
    db.add(owner)
    try:
        db.flush()
        write_audit(db, request, action="owner_bootstrapped", success=True, owner_id=owner.id)
        write_security_event(db, request, event_type="owner_bootstrapped", success=True, owner_id=owner.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise APIError(409, "owner_exists", "The owner has already been created.") from None
    return serialize_owner(owner)


@router.post("/login", response_model=SessionResponse)
def login(
    credentials: LoginCredentials,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    verify_origin(request)
    owner = login_owner(db, request, credentials.email, credentials.password)
    if owner is None:
        write_security_event(db, request, event_type="login_failed", success=False)
        db.commit()
        raise APIError(401, "invalid_credentials", "Invalid email or password.")

    device, new_device_token = resolve_device(db, owner, request)
    session, raw_token, csrf_token = create_session(db, owner, request, device)
    write_security_event(
        db, request, event_type="login_succeeded", success=True,
        owner_id=owner.id, session_id=session.id,
    )
    write_audit(db, request, action="login", success=True, owner_id=owner.id, session_id=session.id)
    db.commit()

    set_session_cookies(response, raw_token, csrf_token)
    if new_device_token is not None:
        set_device_cookie(response, new_device_token)
    return {
        "owner": serialize_owner(owner),
        "expires_at": session.expires_at,
        "csrf_token": csrf_token,
    }


@router.get("/me", response_model=OwnerResponse)
def current_owner(auth: AuthenticatedSession = Depends(get_current_auth)) -> dict[str, object]:
    return serialize_owner(auth.owner)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    auth.session.revoked_at = utcnow()
    auth.session.revoke_reason = "logout"
    write_security_event(
        db, request, event_type="logout", success=True,
        owner_id=auth.owner.id, session_id=auth.session.id,
    )
    write_audit(db, request, action="logout", success=True, owner_id=auth.owner.id, session_id=auth.session.id)
    db.commit()
    clear_auth_cookies(response)
    return {"message": "Logged out."}


@router.post("/password", response_model=PasswordChangeResponse)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    try:
        revoked = accounts.change_password(
            db, request, auth.owner, auth.session.id, body.current_password, body.new_password
        )
    except APIError:
        db.commit()  # persist the failed-attempt counter before answering
        raise
    write_security_event(
        db, request, event_type="password_changed", success=True,
        owner_id=auth.owner.id, session_id=auth.session.id, metadata={"revoked_sessions": revoked},
    )
    write_audit(
        db, request, action="password_changed", success=True,
        owner_id=auth.owner.id, session_id=auth.session.id,
    )
    db.commit()
    return {"message": "Password changed. Other sessions were signed out.", "revoked_sessions": revoked}


@router.post("/password/reset-request", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    body: ResetRequest,
    request: Request,
    db: DBSession = Depends(get_db),
    notifier: NotificationProvider = Depends(get_notifier),
) -> dict[str, str]:
    verify_origin(request)
    accounts.request_password_reset(db, request, body.email, notifier)
    db.commit()
    return {"message": "If recovery is available for this account, instructions will be sent."}


@router.post("/password/reset", response_model=MessageResponse)
def confirm_password_reset(
    body: ResetConfirmRequest,
    request: Request,
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    verify_origin(request)
    try:
        accounts.confirm_password_reset(db, request, body.token, body.new_password)
    except APIError:
        db.commit()
        raise
    write_audit(db, request, action="password_reset_completed", success=True)
    db.commit()
    return {"message": "Password has been reset. Sign in again."}


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    items = [
        SessionItem(
            id=session.id,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            user_agent=session.user_agent,
            current=session.id == auth.session.id,
        )
        for session in accounts.list_sessions(db, auth.owner.id)
    ]
    return {"sessions": items}


@router.post("/sessions/revoke-others", response_model=MessageResponse)
def revoke_other_sessions(
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    revoked = revoke_sessions(db, auth.owner.id, "revoked_by_owner", keep_session_id=auth.session.id)
    write_audit(
        db, request, action="sessions_revoked", success=True, owner_id=auth.owner.id,
        session_id=auth.session.id, metadata={"count": revoked},
    )
    db.commit()
    return {"message": f"Signed out {revoked} other session(s)."}


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_session(
    session_id: UUID,
    request: Request,
    response: Response,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    is_current = session_id == auth.session.id
    if not accounts.revoke_session(db, auth.owner.id, session_id, "revoked_by_owner"):
        raise APIError(404, "session_not_found", "Session not found.")
    write_audit(
        db, request, action="session_revoked", success=True, owner_id=auth.owner.id,
        session_id=auth.session.id, target_type="session", target_id=str(session_id),
    )
    db.commit()
    if is_current:
        clear_auth_cookies(response)
        return {"message": "Signed out."}
    return {"message": "Session signed out."}


@router.get("/devices", response_model=DeviceListResponse)
def list_devices(
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    items = [
        DeviceItem(
            id=device.id,
            display_name=device.display_name,
            created_at=device.created_at,
            last_seen_at=device.last_seen_at,
            revoked=device.revoked_at is not None,
            current=device.id == auth.session.device_id,
        )
        for device in accounts.list_devices(db, auth.owner.id)
    ]
    return {"devices": items}


@router.post("/devices/{device_id}/revoke", response_model=MessageResponse)
def revoke_device(
    device_id: UUID,
    request: Request,
    response: Response,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, str]:
    is_current = device_id == auth.session.device_id
    if not accounts.revoke_device(db, auth.owner.id, device_id):
        raise APIError(404, "device_not_found", "Device not found.")
    write_audit(
        db, request, action="device_revoked", success=True, owner_id=auth.owner.id,
        session_id=auth.session.id, target_type="device", target_id=str(device_id),
    )
    db.commit()
    if is_current:
        clear_auth_cookies(response)
        return {"message": "This device was revoked. Signed out."}
    return {"message": "Device revoked and its sessions signed out."}
