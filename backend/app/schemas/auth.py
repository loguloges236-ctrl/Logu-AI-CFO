from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.security.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("A valid email address is required.")
    return normalized


class LoginCredentials(BaseModel):
    # Length is not policed on login, so the policy is not revealed by error shape.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class BootstrapCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class OwnerResponse(BaseModel):
    id: UUID
    email: str
    default_currency: str
    voice_unlock_enabled: bool


class SessionResponse(BaseModel):
    owner: OwnerResponse
    expires_at: datetime
    csrf_token: str


class MessageResponse(BaseModel):
    message: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PasswordChangeResponse(MessageResponse):
    revoked_sessions: int


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class ResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class SessionItem(BaseModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    current: bool


class SessionListResponse(BaseModel):
    sessions: list[SessionItem]


class DeviceItem(BaseModel):
    id: UUID
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime
    revoked: bool
    current: bool


class DeviceListResponse(BaseModel):
    devices: list[DeviceItem]
