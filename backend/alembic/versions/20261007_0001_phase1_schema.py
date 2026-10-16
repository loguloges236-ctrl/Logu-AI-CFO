"""Phase 1 secure foundation schema (explicit DDL, PostgreSQL).

Revision ID: 20261007_0001
Revises:
Create Date: 2026-10-07

This revision was rewritten from a create_all()-based stub before any environment was deployed.
Its DDL is spelled out here deliberately: a migration must not depend on the current ORM metadata,
or future model changes would silently change what historical revisions create.

Literal enum lists below are part of the schema and must not be imported from application code.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261007_0001"
down_revision = None
branch_labels = None
depends_on = None

_DOCUMENT_KINDS = (
    "bill",
    "invoice",
    "receipt",
    "purchase_order",
    "bank_statement",
    "salary",
    "emi",
    "interest",
    "other",
)
_DOCUMENT_STATUSES = ("pending", "active", "quarantined", "revoked")
_SCAN_STATUSES = ("pending", "scanning", "clean", "infected", "failed")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("singleton_guard", sa.Boolean(), nullable=False, unique=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "password_changed_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("default_currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("voice_unlock_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("voice_provider_reference", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("singleton_guard = true", name="ck_owners_singleton"),
        sa.CheckConstraint("char_length(default_currency) = 3", name="ck_owners_currency_length"),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identifier_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_devices_owner_last_seen", "devices", ["owner_id", "last_seen_at"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=40), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_sessions_owner_revoked", "sessions", ["owner_id", "revoked_at"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_owner", "password_reset_tokens", ["owner_id"])

    op.create_table(
        "auth_failures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    op.create_index("ix_auth_failures_scope_created", "auth_failures", ["scope_key", "created_at"])

    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), sa.ForeignKey("owners.id"), nullable=True),
        sa.Column("session_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    op.create_index("ix_security_events_owner_created", "security_events", ["owner_id", "created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), sa.ForeignKey("owners.id"), nullable=True),
        sa.Column("session_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    op.create_index("ix_audit_logs_owner_created", "audit_logs", ["owner_id", "created_at"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(f"kind IN ({_in_list(_DOCUMENT_KINDS)})", name="ck_documents_kind"),
        sa.CheckConstraint(f"status IN ({_in_list(_DOCUMENT_STATUSES)})", name="ck_documents_status"),
    )
    op.create_index("ix_documents_owner_created", "documents", ["owner_id", "created_at"])

    op.create_table(
        "document_files",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_content_type", sa.String(length=100), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("scan_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("scan_signature", sa.String(length=255), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encryption_scheme", sa.String(length=30), nullable=True),
        sa.Column("storage_deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("size_bytes > 0", name="ck_document_files_positive_size"),
        sa.CheckConstraint(
            f"scan_status IN ({_in_list(_SCAN_STATUSES)})", name="ck_document_files_scan_status"
        ),
    )
    op.create_index("ix_document_files_document", "document_files", ["document_id"])
    op.create_index("ix_document_files_scan_created", "document_files", ["scan_status", "created_at"])

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("categories_json", postgresql.JSONB(), nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
    )

    # Append-only enforcement for audit tables. Rows cannot be updated, deleted or truncated through
    # normal SQL by the application role. A table owner or superuser can still drop the trigger, so
    # production must run the application as a non-owner role (see docs/DATABASE.md).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_reject_append_only_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table %: % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
    for table in ("audit_logs", "security_events"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only_row
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION logu_reject_append_only_change();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION logu_reject_append_only_change();
            """
        )


def downgrade() -> None:
    # Destructive rollback is deliberately unsupported. Restore from a verified backup instead
    # (docs/DEPLOYMENT.md). Never replace this with drop_all().
    raise RuntimeError(
        "Downgrade of 20261007_0001 is disabled. Restore a verified backup to roll back."
    )
