# Logu AI CFO — Phase 1 to 3

A standalone, single-owner financial assistant foundation for Logu. This repository contains **Phases 1 to 3**: secure owner sign-in, session and device management, an encrypted and malware-scanned document vault, and the interfaces that later phases will implement.

> **Status:** Phase 1 is implemented in code. Several verification steps could not be run in the environment where it was written (PostgreSQL integration tests, Alembic upgrade, Docker, ClamAV, Next.js build). See [docs/ROADMAP.md](docs/ROADMAP.md) and the final status report for the exact list.

## Phase 3 (sales, purchase and inventory)

Products and SKUs, warehouses and stock with a traceable movement ledger, purchase requests, orders, goods receipts and bills, quotations, sales orders and invoices, a provider-independent document-intake pipeline with manual review, and controlled posting into the Phase 2 ledger. No payment execution and no bundled OCR. See [docs/COMMERCE.md](docs/COMMERCE.md). Verification is partial; see [docs/ROADMAP.md](docs/ROADMAP.md).

## Phase 2 (core accounting foundation)

Double-entry accounting: chart of accounts, journal entries with draft and posting, reversals, accounting periods, general ledger, trial balance, profit and loss, balance sheet and cash position. Rules live in `backend/app/accounting/domain.py`. See [docs/ACCOUNTING.md](docs/ACCOUNTING.md). Verification is partial; see [docs/ROADMAP.md](docs/ROADMAP.md).

## What Phase 1 provides

- Password sign-in for one owner, with Argon2 hashing, constant-time unknown-user handling, and throttling that does not permanently lock the account.
- Opaque server-side sessions with absolute and idle expiry, a device-binding cookie, double-submit CSRF protection bound to the session, and Origin checking.
- Session and device listing and revocation; password change and an operator-run password reset.
- Document upload with magic-byte type detection, a ClamAV scan (fail-closed), explicit scan states, Fernet encryption at rest, and audited downloads.
- Append-only audit and security-event tables enforced by PostgreSQL triggers.
- A Next.js (App Router) mobile-first interface with a server-side auth guard.
- Docker Compose with network isolation, a Caddy reverse proxy, and a GitHub Actions workflow.

Not implemented: voice unlock (it is reported honestly as *not yet implemented*), EMI, debt advisor, bank integration, payment execution, and document OCR (no provider is bundled). Bank access, when added, must be read-only with no transfer or payment capability.

## Repository layout

```
backend/            FastAPI application, Alembic migrations, tests
  alembic/          Explicit migrations (no create_all / drop_all)
  app/              core, db, models, schemas, services, security, storage, api
  tests/            unit (stdlib-runnable) and integration (PostgreSQL) tests
frontend/           Next.js 15 / React 19 / TypeScript application
docker/Caddyfile    Reverse proxy routes (/api/* to backend, everything else to frontend)
docker-compose.yml  db, clamav, migrate, backend, frontend, caddy
.env.example        Documented configuration; secrets are placeholders
docs/               Architecture, security, database, deployment, roadmap
.github/workflows/  CI definition
```

## Quick start (development)

1. Copy `.env.example` to `.env` and replace every placeholder secret. Do not commit `.env`.
2. Start PostgreSQL and ClamAV, apply migrations, then create the owner account through the bootstrap flow described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
3. Backend tests: `cd backend && pip install -r requirements-dev.txt && python -m unittest discover -s tests/unit -t .`; integration tests need `TEST_DATABASE_URL` pointing to a disposable PostgreSQL database.
4. Frontend: `cd frontend && npm ci && npm run lint && npm run typecheck && npm run build`. `npm ci` requires a `package-lock.json`, which is **not yet committed** (see the roadmap).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Database](docs/DATABASE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Core accounting (Phase 2)](docs/ACCOUNTING.md)
- [Sales, purchase and inventory (Phase 3)](docs/COMMERCE.md)
- [Roadmap and status](docs/ROADMAP.md)
