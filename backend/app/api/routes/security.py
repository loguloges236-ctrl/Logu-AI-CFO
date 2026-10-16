from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DBSession

from app.core.errors import APIError
from app.db.session import get_db
from app.schemas.security import VoiceUnlockStatus, VoiceUnlockUpdate
from app.services.audit import write_audit, write_security_event
from app.services.auth import AuthenticatedSession, get_current_auth, require_csrf

router = APIRouter(prefix="/security", tags=["security"])

_NOT_IMPLEMENTED_MESSAGE = (
    "Voice unlock is not yet implemented: no approved speaker-verification or anti-spoofing "
    "provider is configured. Use your password."
)


def _status(enabled: bool) -> dict[str, object]:
    return VoiceUnlockStatus(enabled=enabled).model_dump()


@router.get("/voice-unlock", response_model=VoiceUnlockStatus)
def get_voice_unlock(auth: AuthenticatedSession = Depends(get_current_auth)) -> dict[str, object]:
    # Always false: the stored flag can only be set by a provider that does not exist yet.
    return _status(enabled=False)


@router.put("/voice-unlock", response_model=VoiceUnlockStatus)
def set_voice_unlock(
    body: VoiceUnlockUpdate,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    if body.enabled:
        raise APIError(409, "voice_provider_not_configured", _NOT_IMPLEMENTED_MESSAGE)
    auth.owner.voice_unlock_enabled = False
    write_security_event(
        db, request, event_type="voice_unlock_setting_changed", success=True,
        owner_id=auth.owner.id, session_id=auth.session.id, metadata={"enabled": False},
    )
    write_audit(
        db, request, action="voice_unlock_setting_changed", success=True,
        owner_id=auth.owner.id, session_id=auth.session.id, metadata={"enabled": False},
    )
    db.commit()
    return _status(enabled=False)


@router.post("/voice-unlock/enroll", status_code=501)
def enroll_voice_unavailable(
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
) -> None:
    write_security_event(
        db, request, event_type="voice_enrollment_requested", success=False,
        owner_id=auth.owner.id, session_id=auth.session.id,
    )
    write_audit(
        db, request, action="voice_enrollment_requested", success=False,
        owner_id=auth.owner.id, session_id=auth.session.id,
    )
    db.commit()
    raise APIError(501, "voice_enrollment_not_implemented", _NOT_IMPLEMENTED_MESSAGE)
