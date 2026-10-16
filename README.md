# Logu AI CFO

Logu AI CFO is a single-owner finance and operations workspace for the Lorav business. It combines secure owner access, accounting controls, debt/EMI schedules, bank-import review boundaries, and sales/purchase/inventory workflows.

**Build status:** The main-branch GitHub Actions workflow passed all 20 steps on 16 October 2026, including backend security audit, lint, unit tests, PostgreSQL integration tests, production frontend audit, frontend lint/typecheck/build, Docker Compose validation, and backend/frontend image builds. View [GitHub Actions](https://github.com/loguloges236-ctrl/Logu-AI-CFO/actions).

## Implemented scope

### Phase 1: Owner access and document vault
- Argon2 password hashing, single-owner bootstrap, opaque server-side sessions, device/session revocation, CSRF/Origin protections, and throttling.
- Encrypted document storage, magic-byte content detection, ClamAV fail-closed scanning, audited downloads, and append-only security/audit events.
- Mobile-first Next.js interface with server-side authentication guard.

### Phase 2: Core finance and accounting
- Double-entry accounting, chart of accounts, journal drafts/posting/reversals, accounting periods, general ledger, trial balance, P&L, balance sheet, and cash position.
- Debt and EMI/payment-obligation schedules, payment records, priority/forecast/settlement rules, notifications, and read-only bank transaction import/reconciliation suggestions.
- Bank data is a read-only boundary. Reconciliation suggestions require owner confirmation; no transfers, payment execution, or bank credentials are implemented.

### Phase 3: Sales, purchasing, and inventory
- Product/SKU catalogue, warehouse/stock locations, append-only stock movement ledger, purchase requests/orders, goods receipts/bills, quotations, sales orders/invoices, and controlled posting into the accounting ledger.
- Document intake and review pipeline with an explicit unavailable provider where OCR/document AI is not configured.

See [Accounting](docs/ACCOUNTING.md), [Commerce](docs/COMMERCE.md), [Security](docs/SECURITY.md), and [Roadmap/status](docs/ROADMAP.md).

## Security posture and known limits

- Python runtime dependency audit passes in CI.
- Production npm dependency audit passes in CI. The full developer dependency tree still reports the high-severity `braces` advisory (GHSA-vfj7-8cjw-p6xm) through ESLint/Next lint tooling. As of 16 October 2026 the upstream advisory lists no patched release; it is dev-only and is documented in [Security](docs/SECURITY.md). Do not interpret this as a clean unrestricted `npm audit`.
- Voice biometric unlock is not implemented.
- No production bank provider, payment execution, bundled OCR/LLM provider, email/SMS delivery, full GST/tax engine, or FIFO inventory valuation is included.
- Full CI proves build/test behavior in disposable infrastructure. A live deployment with real secrets, DNS/TLS, production storage, and the real ClamAV container has not been performed.

## Repository layout

```
backend/              FastAPI application, explicit Alembic migrations, tests
frontend/             Next.js App Router, React, TypeScript, package-lock.json
docker/Caddyfile      Private service routing through Caddy
docker-compose.yml    PostgreSQL, ClamAV, migrations, API, frontend, proxy
.env.example          Placeholder-only configuration template
docs/                 Architecture, database, security, deployment, accounting, commerce
.github/workflows/    CI, dependency audits, test and image-build pipeline
```

## Development

Requirements: Python 3.13, Node.js 22, Docker Compose, and a disposable PostgreSQL 16 instance for integration tests.

1. Copy `.env.example` to `.env`; generate unique secrets for the environment. Never commit `.env` or real keys.
2. Follow [Deployment](docs/DEPLOYMENT.md) for first start, migrations, and owner bootstrap.
3. Backend tests: `cd backend && pip install -r requirements-dev.txt && python -m unittest discover -s tests/unit -t .`. PostgreSQL integration tests need `TEST_DATABASE_URL` pointing at a disposable database.
4. Frontend: `cd frontend && npm ci && npm run lint && npm run typecheck && npm run build`.
5. Main-branch pushes run the full CI workflow: [Actions](https://github.com/loguloges236-ctrl/Logu-AI-CFO/actions).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Database](docs/DATABASE.md)
- [Deployment and operations](docs/DEPLOYMENT.md)
- [Core accounting](docs/ACCOUNTING.md)
- [Sales, purchasing and inventory](docs/COMMERCE.md)
- [Roadmap and verification status](docs/ROADMAP.md)
