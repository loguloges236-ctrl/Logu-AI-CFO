# Roadmap and verification status

**Last verified:** 16 October 2026  
**Automated verification:** [GitHub Actions](https://github.com/loguloges236-ctrl/Logu-AI-CFO/actions)  
**Latest full workflow:** all 20 steps passed. This is CI verification, not a live production deployment.

## Phase 1: identity, sessions, and document vault

**Status: implemented in code and CI-verified.**

Verified in the main-branch workflow:
- Python lint, unit tests, and runtime dependency audit.
- PostgreSQL-backed integration tests.
- Frontend install, lint, TypeScript semantic checking, and production Next.js build.
- Docker Compose configuration validation and application image builds.

Capabilities include owner sign-in, protected sessions and devices, CSRF/Origin checks, rate limiting, encrypted/scanned document intake, audited downloads, and append-only audit/security-event tables.

Remaining operational verification: exercise the full compose stack on the target server with generated environment secrets, real ClamAV signatures, and real browser/device flows.

## Phase 2: finance and accounting

**Status: implemented in code; unit and PostgreSQL integration suites pass in CI.**

Includes double-entry journal posting/reversals, accounting periods, ledger and financial statements, debt/EMI schedules, payment obligations/records, settlement and forecast rules, notification records, and read-only bank import/reconciliation suggestions.

Boundaries:
- Bank import/reconciliation is read-only. It does not move funds or initiate payments.
- Accounting tax fields are metadata; this is not a complete GST/tax calculation or filing engine.
- Do not enable live bank access without provider-specific threat review, credential isolation, and explicit read-only scopes.

Remaining verification: load/concurrency testing against a representative deployment, restore/recovery rehearsal, and full UI acceptance tests with real owner data.

## Phase 3: sales, purchasing, and inventory

**Status: implemented in code; integration and frontend verification pass in CI.**

Includes catalogue/SKUs, warehouses/locations, traceable stock movements, purchase requests/orders, goods receipt and bills, quotations, sales orders/invoices, document intake/review, and controlled ledger posting. Approval creates drafts and does not trigger payments.

Boundaries:
- Payment execution is intentionally absent.
- No full OCR/Document AI provider is bundled; the interface has an explicit unavailable path and manual-review flow.
- FIFO inventory valuation is not implemented.

## CI checks that passed

The verified workflow on 16 October 2026 completed all steps successfully:
1. Install backend dependencies.
2. Apply automatic Ruff fixes and commit source cleanups if needed.
3. Python runtime dependency audit (pip-audit).
4. Ruff lint.
5. Backend unit tests.
6. PostgreSQL integration tests, including migration/ORM and finance/commerce/auth flows.
7. Generate/refresh frontend lockfile.
8. npm ci.
9. Production npm dependency audit.
10. Frontend lint.
11. TypeScript typecheck.
12. Next.js production build.
13. Docker Compose configuration validation.
14. Backend and frontend Docker image builds.

A separate container security workflow now rebuilds both application images on pushes, pull requests, and weekly, then scans OS/library dependencies for known critical vulnerabilities. It fails the scan job on critical findings after excluding vulnerabilities without a fix, and uploads SARIF results to GitHub code scanning when repository permissions/settings allow. Weekly Dependabot updates are configured for Python, npm, Docker base images, and GitHub Actions.

## Known security and deployment limitations

- The full npm development dependency graph reports the high-severity braces advisory GHSA-vfj7-8cjw-p6xm through the ESLint/Next lint toolchain. As of 16 October 2026 the upstream advisory lists no patched release. Production dependencies pass the production-only npm audit; the dev-tree issue is tracked and is not claimed as fixed.
- Voice biometric unlock/enrolment/anti-spoofing is not implemented.
- Password reset delivery over email/SMS is not integrated; recovery is operator-run.
- The project has not been deployed to a live server. Target DNS/TLS, real secrets, persistent upload storage, real ClamAV scanning, backup/restore, and production monitoring need environment-specific acceptance tests.
- No load test or recovery drill is currently part of CI.
- Database-level role separation, field/file key rotation, nonce-based CSP, and operational access logs remain hardening work.

## Recommended next steps

1. Deploy first to a private staging server; generate and store secrets securely and keep edge ports bound to loopback until a reverse proxy/TLS plan is configured.
2. Run first-start and owner-bootstrap steps from [Deployment](DEPLOYMENT.md), verify /health/ready, upload/scan/download a harmless test document, and test recovery/revocation.
3. Rehearse PostgreSQL and upload-volume backups and restore.
4. Run the new container scan workflow and address any critical image findings before staging.
5. Reassess the braces advisory when upstream publishes a patch; do not use npm audit fix --force just to silence it.