"""Migration, schema-parity and PostgreSQL-behaviour tests. Require TEST_DATABASE_URL (PostgreSQL)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.session import get_engine
from tests.conftest import EMAIL, _alembic_config  # noqa: F401  (helper reuse)


def test_fresh_database_migrates_to_head(fresh_db):
    with get_engine().connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "20261009_0009"


def test_migrated_schema_matches_orm_metadata(fresh_db):
    import app.models.entities  # noqa: F401

    def include_object(obj, name, type_, reflected, compare_to):
        # Explicit SQL migrations also own query indexes and uniqueness guards that intentionally
        # are not duplicated in SQLAlchemy metadata. Still compare every mapped table/column/type.
        if reflected and compare_to is None and type_ in {"index", "unique_constraint"}:
            return False
        return True

    with get_engine().connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": include_object}
        )
        differences = compare_metadata(context, Base.metadata)
    assert differences == [], differences


def test_downgrade_is_refused(fresh_db):
    with pytest.raises(Exception) as exc_info:
        command.downgrade(_alembic_config(), "base")
    assert "disabled" in str(exc_info.value).lower()


def test_audit_rows_cannot_be_updated_or_deleted(fresh_db):
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_logs (id, action, success, metadata_json, created_at) "
                "VALUES (gen_random_uuid(), 'probe', true, '{}'::jsonb, now())"
            )
        )
    with engine.begin() as connection:
        with pytest.raises((DBAPIError, ProgrammingError)) as update_error:
            connection.execute(text("UPDATE audit_logs SET action = 'tampered'"))
        assert "append-only" in str(update_error.value)
    with engine.begin() as connection:
        with pytest.raises((DBAPIError, ProgrammingError)):
            connection.execute(text("DELETE FROM audit_logs"))
    with engine.begin() as connection:
        with pytest.raises((DBAPIError, ProgrammingError)):
            connection.execute(text("TRUNCATE audit_logs"))


def test_security_events_are_append_only(fresh_db):
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO security_events (id, event_type, success, metadata_json, created_at) "
                "VALUES (gen_random_uuid(), 'probe', true, '{}'::jsonb, now())"
            )
        )
    with get_engine().begin() as connection:
        with pytest.raises((DBAPIError, ProgrammingError)):
            connection.execute(text("UPDATE security_events SET event_type = 'x'"))


def test_single_owner_is_enforced_by_schema(fresh_db):
    insert = (
        "INSERT INTO owners (id, singleton_guard, email, password_hash, password_changed_at, "
        "default_currency, is_active, voice_unlock_enabled, created_at, updated_at) "
        "VALUES (gen_random_uuid(), true, :email, 'x', now(), 'INR', true, false, now(), now())"
    )
    with get_engine().begin() as connection:
        connection.execute(text(insert), {"email": EMAIL})
    with pytest.raises(IntegrityError):
        with get_engine().begin() as connection:
            connection.execute(text(insert), {"email": "second@example.com"})


def test_document_status_and_scan_status_check_constraints(fresh_db):
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO owners (id, singleton_guard, email, password_hash, password_changed_at, "
                "default_currency, is_active, voice_unlock_enabled, created_at, updated_at) "
                "VALUES (gen_random_uuid(), true, :email, 'x', now(), 'INR', true, false, now(), now())"
            ),
            {"email": EMAIL},
        )
        owner_id = connection.execute(text("SELECT id FROM owners")).scalar_one()
    with pytest.raises(IntegrityError):
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (id, owner_id, kind, status, display_name, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :o, 'bill', 'bogus', 'a.pdf', now(), now())"
                ),
                {"o": owner_id},
            )
    with pytest.raises(IntegrityError):
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (id, owner_id, kind, status, display_name, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :o, 'not_a_kind', 'pending', 'a.pdf', now(), now())"
                ),
                {"o": owner_id},
            )


def test_jsonb_columns_are_jsonb(fresh_db):
    with get_engine().connect() as connection:
        data_type = connection.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'audit_logs' AND column_name = 'metadata_json'"
            )
        ).scalar_one()
    assert data_type == "jsonb"
