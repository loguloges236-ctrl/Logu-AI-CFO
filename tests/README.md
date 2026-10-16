# Tests

Backend tests live in `backend/tests/`, not in this directory. This folder is reserved for repository-level checks and is currently empty.

- `backend/tests/unit/`: pure-Python tests. Run with `cd backend && python -m unittest discover -s tests/unit -t .`. These require only `pydantic`, `cryptography`, and the standard library. **63 tests pass.**
- `backend/tests/integration/`: require `TEST_DATABASE_URL` pointing to a disposable PostgreSQL 16 database; the fixture drops and recreates the schema, then runs `alembic upgrade head`. Run with `pytest backend/tests/integration`. **Not executed in the environment where Phase 1 was written.**

Never point `TEST_DATABASE_URL` at a database containing real data.

Phase 2 accounting tests:

- `backend/tests/unit/test_accounting_domain.py`, `test_accounting_schemas.py`, `test_accounting_migration_static.py`: runnable without PostgreSQL.
- `backend/tests/integration/test_accounting_flows.py`: API flows against PostgreSQL. **Not executed yet.**

## Phase 3 tests

- `backend/tests/unit/test_commerce_domain.py`, `test_commerce_extraction.py`: pure rules. Runnable without PostgreSQL.
- `backend/tests/unit/test_commerce_static.py`: ORM-to-migration column parity and enum parity; needs no database.
- `backend/tests/unit/test_commerce_schemas.py`, `test_commerce_routes_static.py`: request schemas and the route authentication and CSRF guard.
- `backend/tests/sql/test_commerce_rules.py`: database rules on PostgreSQL. Set `COMMERCE_SQL_TEST_ADMIN_URI` to a disposable PostgreSQL server (the test creates and drops its own database). Skipped when unset. **39 passing on PostgreSQL 16.15.**
- `backend/tests/integration/test_commerce_flows.py`: API flows. **Written, not executed.**
