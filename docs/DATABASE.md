# Database

PostgreSQL 16 is the only supported database. The backend refuses other dialects. Schema changes are made **only** through Alembic migrations with explicit DDL. `create_all` and `drop_all` are not used by application code.

## Migrations

- Location: `backend/alembic/versions/`.
- Current revision: `20261007_0001_phase1_schema` creates all Phase 1 tables, indexes, check constraints, and the append-only trigger function.
- `alembic/env.py` reads the URL from `get_settings().database_url` only. `alembic.ini` contains no credentials.
- The downgrade for the Phase 1 revision raises `RuntimeError`. Phase 1 is not reversible in place; restore from backup instead.
- Commands: `alembic upgrade head`, `alembic check` (compare metadata), `alembic current`.

## Tables

| Table | Purpose | Notes |
|---|---|---|
| `owners` | The single owner | Singleton guard (unique constant + check constraint) |
| `devices` | Remembered browsers | Revoking a device cascades to its sessions |
| `sessions` | Opaque server-side sessions | `token_hash`, `csrf_token_hash`, `ip_hash`, `revoke_reason`, absolute and idle expiry |
| `password_reset_tokens` | Single-use reset tokens | Stores only keyed hashes; invalidated when another is used |
| `auth_failures` | Throttle counters | Bounded; purged after one day |
| `security_events` | Security-relevant events | Append-only (trigger) |
| `audit_logs` | Business and access audit | Append-only (trigger); `metadata` is JSONB |
| `documents` | Logical documents | `status`: pending, active, quarantined, revoked |
| `document_files` | Stored files | `storage_key` unique; `scan_status`: pending, scanning, clean, infected, failed; `sha256` of plaintext; `encryption_scheme`; `storage_deleted_at` |
| `notification_preferences` | Placeholder for later phases | No delivery implemented |
| `system_settings` | Key/value settings | Used sparingly |

## Append-only enforcement

`logu_reject_append_only_change()` raises an exception on `UPDATE`, `DELETE` (row-level) and `TRUNCATE` (statement-level) for `audit_logs` and `security_events`.

**Limitation:** the owner role (`logu`) and superusers can still disable triggers. The runtime role (`logu_app`, below) cannot. Operational guidance is in [DEPLOYMENT.md](DEPLOYMENT.md).

## Runtime role (least privilege, revision `20261009_0009`)

Two database roles, with separate jobs:

| Role | Used by | Can | Cannot |
|---|---|---|---|
| `logu` (owner) | `migrate` service, operator commands that change schema, `alembic` | Owns the database, schema and tables; runs DDL | Nothing is withheld; it is the trusted migration role |
| `logu_app` (runtime) | `backend` service (once switched, see DEPLOYMENT.md) | `SELECT, INSERT, UPDATE, DELETE` on tables; `USAGE, SELECT` on sequences; `CONNECT` on the database; `USAGE` on schema `public` | DDL (`CREATE`, `ALTER`, `DROP`, `TRUNCATE`), ownership, `GRANT`, role or database creation, `alembic_version` access, superuser or `BYPASSRLS` |

- Role setup: `database/roles/provision_roles.sql`. It prompts for the password (never on the command line), is safe to re-run, and does not drop or change data.
- Migration `20261009_0009` applies the table and sequence grants when `logu_app` exists. Without the role it does nothing, so local development and test databases still work with the owner alone. The role name comes from the session setting `logu.runtime_role` (default `logu_app`); tests use this to point at a disposable role.
- Default privileges (`ALTER DEFAULT PRIVILEGES`) give `logu_app` the same DML grants on tables and sequences created later by the owner, so future migrations do not need a manual grant step.
- `REVOKE ALL ON alembic_version` keeps migration state with the owner.
- Downgrade is refused. Rollback is a manual, reviewed `REVOKE` (see DEPLOYMENT.md). Nothing is dropped automatically.
- Tests: `backend/tests/sql/test_runtime_role_privileges.py` (11 tests). They run the real migration as the owner, then assert with `SET ROLE` inside a superuser session that the runtime role keeps every data operation, is denied each DDL, trigger, ownership, grant, role and migration-state action, and that the append-only trigger still blocks `UPDATE` and `DELETE` for it. They need `COMMERCE_SQL_TEST_ADMIN_URI`.
- Not covered by these tests: connecting as `logu_app` over the real network with its password (the provisioning script was checked with a local psql session), and the full FastAPI application running under `logu_app` (needs SQLAlchemy and FastAPI, which are unavailable in the authoring environment).

## Verification status

- Migration definitions are checked by static review and by the pure-Python unit tests where possible.
- `alembic upgrade head` on a real PostgreSQL 16, `alembic check`, and the integration tests in `backend/tests/integration/` **have not been executed** in the environment where Phase 1 was written. See the roadmap for the exact list.

## Phase 2 accounting tables (revision `20261008_0002`)

| Table | Purpose | Key rules |
|---|---|---|
| `accounts` | Chart of accounts | `code` unique (4–10 digits); `category` and subtype checked; self-parent refused; system accounts keep code and category; code/category fixed once used; deletion refused |
| `accounting_periods` | OPEN / CLOSED periods | `start_date <= end_date`; EXCLUDE constraint refuses overlaps; closed periods cannot change or reopen; dates fixed; deletion refused |
| `journal_entries` | Entry header | `entry_number` unique, assigned at posting from `journal_entry_number_seq`; posted entries require posting date and number; `reverses_entry_id` unique (one reversal per entry) |
| `journal_lines` | Entry lines | `debit`, `credit` NUMERIC(18,2) ≥ 0; exactly one side above zero; `(journal_entry_id, line_number)` unique; optional tax metadata |

Triggers (PL/pgSQL, defined in the migration):

- `journal_entries_guard` (BEFORE UPDATE/DELETE): refuses changes and deletion of posted entries; a draft may become posted only inside an OPEN period covering its posting date.
- `journal_lines_guard` (BEFORE INSERT/UPDATE/DELETE): refuses any change to lines of a posted entry.
- `journal_entries_balanced_on_post` (CONSTRAINT TRIGGER, DEFERRABLE INITIALLY DEFERRED): at commit, a posted entry must have at least two lines and equal debit and credit totals.
- `accounts_guard` and `accounting_periods_guard`: as described in the table notes above.

Indexes: `accounts(category)`, `accounts(parent_id)`; `journal_entries(transaction_date)`, `(status)`, `(status, posting_date)`; `journal_lines(account_id)`, `(journal_entry_id)`, unique `(journal_entry_id, line_number)`.

Downgrade is refused (RuntimeError), as in Phase 1.

**Verification status:** The five PL/pgSQL function bodies from this migration were compiled on PostgreSQL 16.15 (syntax only). The table DDL, triggers and EXCLUDE constraint were **not** executed, because SQLAlchemy and Alembic could not be installed in the build environment. See [ROADMAP.md](ROADMAP.md).

## Phase 3 commerce tables (revision `20261008_0003`)

Twenty-three tables, listed in [COMMERCE.md](COMMERCE.md#data-model). Explicit SQL only; downgrade is refused. Triggers enforce append-only stock history (including TRUNCATE), the negative-stock floor, the two-leg transfer balance at commit, draft-only edits and posted-figure freezing, goods-receipt and sales-order line rules, and catalogue uniqueness.

Verified: the migration's SQL and triggers on PostgreSQL 16.15 (39 rule tests). Unverified: the Alembic runner, `alembic check` and ORM-to-database behaviour at runtime. Static parity between ORM and migration columns is checked without a database.
