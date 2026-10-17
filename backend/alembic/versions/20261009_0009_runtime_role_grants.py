"""Least-privilege runtime role: grants only, no DDL and no ownership change.

Revision ID: 20261009_0009
Revises: 20261008_0008

The application connects as a dedicated login role (logu_app by default) that is NOT the database or schema owner.
This migration gives that role data-manipulation privileges on the existing objects, and the matching default
privileges for objects created later by the migration role. It never changes ownership and never grants DDL, TRUNCATE,
REFERENCES, TRIGGER or role membership.

The role name is read from the session setting logu.runtime_role (tests use it). When the setting is empty the name is
logu_app. The role is created once by database/roles/provision_roles.sql. If the role does not exist (local development,
test databases), these statements do nothing, so `alembic upgrade head` keeps working for the owner alone.

Every statement is explicit SQL. Downgrade is refused. To roll back the grants, see docs/DEPLOYMENT.md.
"""

from alembic import op

revision = "20261009_0009"
down_revision = "20261008_0008"
branch_labels = None
depends_on = None

STATEMENTS: tuple[str, ...] = (
    """
    DO $$
    DECLARE
        runtime_role text := coalesce(nullif(current_setting('logu.runtime_role', true), ''), 'logu_app');
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
            RETURN;
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', runtime_role);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', runtime_role);
        EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', runtime_role);
        -- Migration state is read only by the migration role. The runtime role must not alter it.
        IF to_regclass('public.alembic_version') IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON TABLE public.alembic_version FROM %I', runtime_role);
        END IF;
    END
    $$
    """,
    """
    DO $$
    DECLARE
        runtime_role text := coalesce(nullif(current_setting('logu.runtime_role', true), ''), 'logu_app');
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
            RETURN;
        END IF;
        -- Applies to objects created later by the role running migrations (the owner).
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
            runtime_role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
            runtime_role
        );
    END
    $$
    """,
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled: destructive downgrade is refused. Revoke the grants manually (see docs/DEPLOYMENT.md)."
    )
