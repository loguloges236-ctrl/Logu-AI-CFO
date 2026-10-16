"""PostgreSQL ORM entities. The explicit Alembic migration in alembic/versions must match these models.

`tests/integration/test_migrations.py` compares the migrated schema with this metadata.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CreatedAtMixin:
    """For append-only tables: no updated_at, rows are never modified (enforced by trigger)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentKind(enum.StrEnum):
    BILL = "bill"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    PURCHASE_ORDER = "purchase_order"
    BANK_STATEMENT = "bank_statement"
    SALARY = "salary"
    EMI = "emi"
    INTEREST = "interest"
    OTHER = "other"


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"  # received, scan in progress or not yet completed
    ACTIVE = "active"  # scanned CLEAN and stored (encrypted)
    QUARANTINED = "quarantined"  # scan INFECTED or FAILED; no bytes are stored
    REVOKED = "revoked"  # deleted by owner; bytes removed, metadata retained


class FileScanStatus(enum.StrEnum):
    PENDING = "pending"
    SCANNING = "scanning"
    CLEAN = "clean"
    INFECTED = "infected"
    FAILED = "failed"


def check_in(column: str, enum_cls: type[enum.Enum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Owner(TimestampMixin, Base):
    __tablename__ = "owners"
    __table_args__ = (
        CheckConstraint("singleton_guard = true", name="ck_owners_singleton"),
        CheckConstraint("char_length(default_currency) = 3", name="ck_owners_currency_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique + check constraint permit at most one owner row.
    singleton_guard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    voice_unlock_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice_provider_reference: Mapped[str | None] = mapped_column(String(255))


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (Index("ix_devices_owner_last_seen", "owner_id", "last_seen_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    # HMAC digest of the random device cookie value. The raw value never reaches the database.
    identifier_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_owner_revoked", "owner_id", "revoked_at"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class PasswordResetToken(CreatedAtMixin, Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_owner", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthFailure(CreatedAtMixin, Base):
    """Failed-authentication counter rows. Keys are HMAC digests of IP/email scopes, never raw values."""

    __tablename__ = "auth_failures"
    __table_args__ = (Index("ix_auth_failures_scope_created", "scope_key", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)


class SecurityEvent(CreatedAtMixin, Base):
    __tablename__ = "security_events"
    __table_args__ = (Index("ix_security_events_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AuditLog(CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owners.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(100))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(check_in("kind", DocumentKind), name="ck_documents_kind"),
        CheckConstraint(check_in("status", DocumentStatus), name="ck_documents_status"),
        Index("ix_documents_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DocumentStatus.PENDING.value)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentFile(TimestampMixin, Base):
    __tablename__ = "document_files"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_document_files_positive_size"),
        CheckConstraint(check_in("scan_status", FileScanStatus), name="ck_document_files_scan_status"),
        Index("ix_document_files_document", "document_id"),
        Index("ix_document_files_scan_created", "scan_status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Digest of the PLAINTEXT, used to verify integrity after decryption.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FileScanStatus.PENDING.value
    )
    scan_signature: Mapped[str | None] = mapped_column(String(255))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encryption_scheme: Mapped[str | None] = mapped_column(String(30))
    storage_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationPreference(TimestampMixin, Base):
    """Placeholder for Phase 2+ notification opt-ins. No delivery is implemented."""

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    categories_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SystemSetting(TimestampMixin, Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
