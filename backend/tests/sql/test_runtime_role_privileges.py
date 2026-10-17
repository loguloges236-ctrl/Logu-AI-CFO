"""Runtime database role boundary (migration 20261009_0009 and database/roles/provision_roles.sql).

Runs against a disposable PostgreSQL database. Skipped unless COMMERCE_SQL_TEST_ADMIN_URI is set; that connection must be
a superuser, because the test creates and drops roles and databases. The test uses SET ROLE inside one superuser session
instead of logging in as the runtime role, so no passwords or peer-auth configuration are needed.

Proves that:
  * the runtime role keeps every data operation the application performs (insert, select, update, delete, sequences),
  * the runtime role cannot run DDL, change ownership, grant privileges, manage roles or databases, or alter migration state,
  * the append-only trigger still blocks changes even though the runtime role has DML privileges,
  * objects created later by the owner are readable and writable by the runtime role (default privileges),
  * the migration is a no-op when the runtime role does not exist (local development).

UNVERIFIED here: the full application runs with the real schema (migrations 0001-0009). That needs SQLAlchemy and FastAPI,
which are covered by tests/integration in CI.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import uuid
from pathlib import Path

from tests.sql.test_commerce_rules import ADMIN_URI, run_psql, with_database

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "20261009_0009_runtime_role_grants.py"


def load_statements() -> tuple[str, ...]:
    """Read STATEMENTS from the migration without importing Alembic (the migration only uses op.execute)."""
    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(execute=lambda sql: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("runtime_role_grants", MIGRATION)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.STATEMENTS
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved


def script(statements: tuple[str, ...]) -> str:
    return "\n".join(f"{statement};" for statement in statements)


def denied(statement: str, sqlstate: str = "42501") -> str:
    """Passes only if the statement fails with the given SQLSTATE. 42501 = insufficient_privilege."""
    return f"""DO $probe$
BEGIN
    BEGIN
        EXECUTE $q${statement}$q$;
        RAISE EXCEPTION 'UNEXPECTED SUCCESS: %', $q${statement}$q$;
    EXCEPTION WHEN SQLSTATE '{sqlstate}' THEN
        NULL;
    END;
END
$probe$;"""


def allowed(statement: str) -> str:
    return f"{statement};"


# Owner-side fixture: a representative slice of the schema, including an append-only table, a sequence and migration state.
FIXTURE = """
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO alembic_version (version_num) VALUES ('20261008_0008');
CREATE SEQUENCE journal_number_seq START 1 MINVALUE 1;
CREATE TABLE notes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    body text NOT NULL,
    read_at timestamptz
);
CREATE TABLE ledger_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    amount numeric(18,2) NOT NULL CHECK (amount <> 0)
);
CREATE FUNCTION reject_change() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only table' USING ERRCODE = 'P0001';
END
$fn$;
CREATE TRIGGER ledger_lines_append_only
    BEFORE UPDATE OR DELETE ON ledger_lines
    FOR EACH ROW EXECUTE FUNCTION reject_change();
"""


class RuntimeRolePrivilegeTests(unittest.TestCase):
    """One disposable database and one pair of roles for the whole class."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ADMIN_URI:
            raise unittest.SkipTest("COMMERCE_SQL_TEST_ADMIN_URI is not set")
        suffix = uuid.uuid4().hex[:10]
        cls.db = f"logu_rolecheck_{suffix}"
        cls.owner = f"logu_rc_owner_{suffix}"
        cls.app = f"logu_rc_app_{suffix}"
        cls.app_password = "test-only-app-password-0123456789"
        setup = f"""
CREATE ROLE {cls.owner} LOGIN PASSWORD 'test-only-owner-password' NOSUPERUSER;
CREATE ROLE {cls.app} LOGIN PASSWORD '{cls.app_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE {cls.db} OWNER {cls.owner};
"""
        result = run_psql(ADMIN_URI, setup)
        assert result.returncode == 0, result.stderr
        cls.admin_db_uri = with_database(ADMIN_URI, cls.db)

    @classmethod
    def tearDownClass(cls) -> None:
        if not ADMIN_URI:
            return
        run_psql(ADMIN_URI, f"DROP DATABASE IF EXISTS {cls.db} WITH (FORCE);")
        run_psql(ADMIN_URI, f"DROP ROLE IF EXISTS {cls.app};\nDROP ROLE IF EXISTS {cls.owner};")

    def _as_owner_then_migrate(self) -> None:
        """Owner builds the fixture, then runs the real migration statements as the owner (the normal upgrade path)."""
        sql = (
            f"ALTER SCHEMA public OWNER TO {self.owner};\n"
            f"REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n"
            f"SET ROLE {self.owner};\n"
            f"{FIXTURE}\n"
            f"SET logu.runtime_role = '{self.app}';\n"
            f"{script(load_statements())}\n"
            f"RESET ROLE;\n"
        )
        result = run_psql(self.admin_db_uri, sql)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _as_app(self, body: str) -> None:
        """Runs statements as the runtime role inside one superuser session. Any failing statement fails the test."""
        result = run_psql(self.admin_db_uri, f"SET ROLE {self.app};\n{body}\nRESET ROLE;\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    # -- setup -------------------------------------------------------------

    def test_01_setup_and_migration_as_owner(self) -> None:
        self._as_owner_then_migrate()

    def test_02_runtime_role_is_not_owner_or_superuser(self) -> None:
        self._as_app("""
DO $check$
BEGIN
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE EXCEPTION 'runtime role must not be a superuser';
    END IF;
    IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) = current_user THEN
        RAISE EXCEPTION 'runtime role must not own the database';
    END IF;
    IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public') = current_user THEN
        RAISE EXCEPTION 'runtime role must not own schema public';
    END IF;
END
$check$;
""")

    # -- legitimate application operations ----------------------------------

    def test_03_runtime_role_keeps_application_data_operations(self) -> None:
        self._as_app("\n".join([
            allowed("INSERT INTO notes (body) VALUES ('first note')"),
            allowed("UPDATE notes SET read_at = now() WHERE body = 'first note'"),
            allowed("SELECT count(*) FROM notes"),
            allowed("INSERT INTO notes (body) VALUES ('to delete')"),
            allowed("DELETE FROM notes WHERE body = 'to delete'"),
            allowed("INSERT INTO ledger_lines (amount) VALUES (10.00)"),
            allowed("SELECT count(*) FROM ledger_lines"),
            allowed("SELECT nextval('journal_number_seq')"),
        ]))

    def test_04_default_privileges_cover_objects_created_later(self) -> None:
        result = run_psql(
            self.admin_db_uri,
            f"SET ROLE {self.owner};\nCREATE TABLE later_table (id int);\nINSERT INTO later_table VALUES (1);\nRESET ROLE;\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self._as_app("\n".join([
            allowed("SELECT count(*) FROM later_table"),
            allowed("INSERT INTO later_table VALUES (2)"),
        ]))

    # -- prohibited administrative operations -------------------------------

    def test_05_runtime_role_cannot_run_ddl(self) -> None:
        self._as_app("\n".join([
            denied("CREATE TABLE public.attack_table (id int)"),
            denied("ALTER TABLE notes ADD COLUMN attack_column int"),
            denied("DROP TABLE ledger_lines"),
            denied("TRUNCATE notes"),
            denied("CREATE INDEX attack_idx ON notes (body)"),
            denied("CREATE FUNCTION public.attack_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'"),
            denied("CREATE SCHEMA attack_schema"),
            denied("CREATE SEQUENCE attack_seq"),
        ]))

    def test_06_runtime_role_cannot_disable_or_add_triggers(self) -> None:
        self._as_app("\n".join([
            denied("ALTER TABLE ledger_lines DISABLE TRIGGER ledger_lines_append_only"),
            denied("CREATE TRIGGER attack_trg BEFORE INSERT ON notes FOR EACH ROW EXECUTE FUNCTION reject_change()"),
        ]))

    def test_07_runtime_role_cannot_change_ownership_or_grant(self) -> None:
        self._as_app("\n".join([
            denied(f"ALTER TABLE notes OWNER TO {self.app}"),
            denied("ALTER SEQUENCE journal_number_seq RESTART WITH 1"),
        ]))
        # Without a grant option, GRANT is accepted but grants nothing (PostgreSQL only warns). Prove nothing was granted.
        self._as_app("GRANT ALL ON notes TO PUBLIC;")
        check = run_psql(
            self.admin_db_uri,
            """DO $check$
BEGIN
    IF has_table_privilege('public', 'notes', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE') THEN
        RAISE EXCEPTION 'runtime role granted privileges to PUBLIC';
    END IF;
END
$check$;""",
        )
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_08_runtime_role_cannot_manage_roles_or_databases(self) -> None:
        self._as_app(denied("CREATE ROLE attack_role"))
        # CREATE DATABASE cannot run inside a transaction block, so it runs as a top-level statement.
        result = run_psql(self.admin_db_uri, f"SET ROLE {self.app};\nCREATE DATABASE attack_db;\nRESET ROLE;\n")
        self.assertNotEqual(result.returncode, 0, "runtime role was allowed to CREATE DATABASE")
        self.assertIn("permission denied to create database", result.stderr)
        # SET ROLE cannot be probed from this superuser session (superusers may assume any role), so check the invariant:
        # the runtime role is not a member of any role, in particular not of the owner.
        check = run_psql(
            self.admin_db_uri,
            f"""DO $check$
BEGIN
    IF pg_has_role('{self.app}', '{self.owner}', 'MEMBER') THEN
        RAISE EXCEPTION 'runtime role is a member of the owner role';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = '{self.app}'::regrole) THEN
        RAISE EXCEPTION 'runtime role is a member of another role';
    END IF;
END
$check$;""",
        )
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_09_runtime_role_cannot_alter_migration_state(self) -> None:
        self._as_app("\n".join([
            denied("SELECT version_num FROM alembic_version"),
            denied("UPDATE alembic_version SET version_num = 'tampered'"),
            denied("DELETE FROM alembic_version"),
        ]))

    # -- append-only history still holds with DML privileges ----------------

    def test_10_append_only_trigger_still_blocks_changes(self) -> None:
        self._as_app("\n".join([
            denied("UPDATE ledger_lines SET amount = 99.00", sqlstate="P0001"),
            denied("DELETE FROM ledger_lines", sqlstate="P0001"),
        ]))

    # -- migration without the runtime role ---------------------------------

    def test_11_migration_is_noop_without_runtime_role(self) -> None:
        plain_db = f"logu_rolecheck_plain_{uuid.uuid4().hex[:10]}"
        owner = f"logu_rc_plain_owner_{uuid.uuid4().hex[:10]}"
        try:
            setup = f"CREATE ROLE {owner} LOGIN PASSWORD 'test-only-owner-password' NOSUPERUSER;\n"
            setup += f"CREATE DATABASE {plain_db} OWNER {owner};\n"
            self.assertEqual(run_psql(ADMIN_URI, setup).returncode, 0)
            plain_uri = with_database(ADMIN_URI, plain_db)
            absent_role = f"logu_rc_absent_{uuid.uuid4().hex[:10]}"
            sql = (
                f"ALTER SCHEMA public OWNER TO {owner};\n"
                f"SET ROLE {owner};\n"
                f"CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);\n"
                f"CREATE TABLE notes (id int);\n"
                f"SET logu.runtime_role = '{absent_role}';\n"
                f"{script(load_statements())}\n"
                f"RESET ROLE;\n"
            )
            result = run_psql(plain_uri, sql)
            self.assertEqual(result.returncode, 0, result.stderr)
            # With no runtime role present, the migration must not have granted anything: notes keeps its default ACL.
            check = run_psql(
                plain_uri,
                """DO $check$
BEGIN
    IF (SELECT relacl FROM pg_class WHERE relname = 'notes') IS NOT NULL THEN
        RAISE EXCEPTION 'migration granted privileges although the runtime role is absent';
    END IF;
END
$check$;""",
            )
            self.assertEqual(check.returncode, 0, check.stderr)
        finally:
            run_psql(ADMIN_URI, f"DROP DATABASE IF EXISTS {plain_db} WITH (FORCE);\nDROP ROLE IF EXISTS {owner};")


if __name__ == "__main__":
    unittest.main()
