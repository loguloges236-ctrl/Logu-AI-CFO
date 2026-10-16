# Roadmap and Phase 1 status

## Phase 1 status

Phase 1 is **implemented in code but not fully verified**. No completion percentage is given here because the integration behaviour that most affects it could not be executed; the final status report lists what is and is not proven.

### Verified in this environment

- 63 pure-Python unit tests (settings validation, proxy resolution, keyed digests, file rules, crypto, ClamAV reply parsing against a fake clamd server): **passing**.
- Static import check of app-internal imports: **no unresolved imports**.
- TypeScript syntax parse of all 19 frontend TS/TSX files: **no syntax errors**.

### Not verified (could not be executed here)

- PostgreSQL integration tests (`backend/tests/integration/`), including authentication flows, CSRF, authorisation, uploads, scan states, downloads, deletion, headers, proxy handling, rate limiting and production configuration.
- Alembic `upgrade head` and `alembic check` against PostgreSQL 16.
- `ruff check`, `pip-audit` and `npm audit`.
- Frontend `npm ci`, lint, typecheck and `next build`. A full TypeScript semantic check was only partially possible: type packages for React, Next.js and Node were not installed, so results are dominated by missing-module errors and cannot be interpreted as pass or fail.
- Docker image builds, `docker compose config`, Caddy routing, and the ClamAV container.
- The CI workflow (`.github/workflows/ci.yml`) has not been run.

### Blocking gaps

- `frontend/package-lock.json` is not committed. The npm registry returned 403 and the local cache is incomplete (offline generation failed with ENOTCACHED), so `npm ci` will fail in CI until the lockfile is generated with registry access.
- **Production SSR path: resolved in code, not yet exercised end to end.** The frontend's server-side calls to `http://backend:8000` were rejected by the Host allow-list (`invalid_host`), not by `FORCE_HTTPS`. The backend now accepts the single-label internal name from `INTERNAL_API_HOSTS` only when the request did not pass through the trusted proxy. `FORCE_HTTPS` and the public Host list are unchanged. Covered by unit tests; needs a live compose run to confirm.
- The integration suite has not been run against a real database, so the migration and auth paths are not proven.

## Not implemented (intentionally)

- Voice unlock (biometric enrolment, matching, and anti-spoofing). Reported as not implemented.
- Accounting, inventory, invoices, purchase orders, EMI, debt advisor, and bank integration. Phase 2 has not started.
- AI and OCR providers (contracts only).
- Password-reset delivery over e-mail or SMS.

## Next steps

1. Generate and commit `frontend/package-lock.json`.
2. Run the backend integration suite and `alembic check` against PostgreSQL 16.
3. Run `docker compose config` and the image builds, then run the ClamAV scan path end to end.
4. Run `next build` and a full `tsc --noEmit` with dependencies installed.
5. Address the remaining security limitations listed in [SECURITY.md](SECURITY.md) (database role separation, key rotation, nonce-based CSP, access logging).


## Phase 2: core accounting foundation

**Status: code written; verification partial.** Estimated code completeness against the Phase 2 brief: about 80%. Verified at runtime: the pure accounting rules only. Everything else below is UNVERIFIED.

### Verified

- 59 new pure unit tests (`tests/unit/test_accounting_*.py`) covering money parsing and precision, balancing, one-sided lines, negative amounts, reversal netting, hierarchy and category rules, normal balances, trial balance, P&L, balance sheet equation, ledger running balance, period bounds, default-chart consistency, schema validation and the read-only AI boundary.
- Migration static checks: revision chain, no `create_all`/`drop_all`/destructive DDL, NUMERIC(18,2) money, presence of the triggers and EXCLUDE constraint, and enum lists equal to the domain module's lists (drift guard).
- `ruff check` passes on `app`, `tests` and `alembic` under the project configuration (this check had not been run before; 25 safe autofixes were applied across the tree, mostly import order and unused imports, including some Phase 1 files; `str, Enum` was replaced with `StrEnum` in the Phase 1 entity enums and the new accounting enums, with the same `.value`).
- Frontend TypeScript and TSX files parse without syntax errors; every relative import resolves; subtype lists match between backend and frontend.
- The five PL/pgSQL trigger functions compile on PostgreSQL 16.15.

### Unverified (could not run in the build environment)

- The migration `20261008_0002` itself: table DDL, triggers, the EXCLUDE constraint, the sequence. SQLAlchemy and Alembic were not installable.
- All accounting integration tests in `tests/integration/test_accounting_flows.py`, including immutability, closed-period rejection and reversal behaviour at the API level.
- The ORM models against the migration (`test_migrations.py` compares them when run).
- Route behaviour under FastAPI (authentication, CSRF, error mapping).
- Frontend `npm ci`, ESLint, `tsc` and `next build`. The lockfile is still missing (see Phase 1 blockers).

### Known gaps

- Concurrency under load has not been tested.
- Entry-number gaps are expected after failed postings.
- No reporting on tax; tax fields are metadata only.

## Phase 3: sales, purchase and inventory

**Status: code written; verification partial.** Details, limits and the verified/unverified split are in [COMMERCE.md](COMMERCE.md).

### Verified

- 42 pure unit tests for stock, pricing, workflow and extraction rules; 7 static tests (ORM and migration column parity for 23 tables, enum parity with the domain, migration non-destructiveness); 6 request-schema tests; 5 route guard tests (authentication and CSRF on every route). Full unit suite: 193 passing.
- 39 database rule tests on PostgreSQL 16.15: append-only stock history, negative-stock floor, transfer balance, draft-only edits, voiding rules, catalogue uniqueness, and the migration DDL and triggers.
- Syntax of all commerce frontend files.

### Not verified

- The ORM, services against a live session, and the HTTP routes (SQLAlchemy and FastAPI were not installable here).
- The integration flow tests in `backend/tests/integration/test_commerce_flows.py` (written, not run).
- Alembic `upgrade head` and `alembic check`.
- Frontend `npm ci`, lint, typecheck and `next build`; the lockfile is still missing (Phase 1 blocker).

### Not implemented (intentionally)

- Document AI and OCR providers (the interface and an explicit "unavailable" provider only).
- Payment execution, bank integration, a GST engine, FIFO valuation.

### Carry-over

- Phase 1 and 2 integration suites, Docker, ClamAV and the lockfile remain unverified in this environment. See the Phase 1 and Phase 2 sections above.

### Corrections to earlier phases

- Phase 2 accounting service: `_flush` recursed into itself; it now calls `db.flush()`. Posting gained `commit=False` so business events post in one transaction. Default behaviour is unchanged.
