# Logu AI CFO: Completion Tracker

Status labels: Implemented and verified | Implemented but partially verified | Not implemented | Blocked by external dependency | Requires my explicit approval

- Baseline: `main` @ `e3abac3` (2026-10-16), read from a shallow clone in the session. PR heads were read through git (`git ls-remote`, `refs/pull/N/head`); GitHub Actions run state still could not be read.
- Current local branch: `feature/db-runtime-role` (from `main`), uncommitted changes listed under Phase B. Nothing pushed.
- Verification environment for this pass: Linux sandbox. PyPI and npm registries unreachable (DNS fails). GitHub token invalid, so PR and Actions state could not be read.
- This tracker is committed on the local branch `feature/db-runtime-role` only. It is NOT pushed.

## Phase A: Reconcile existing work

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Check PR #19 (frontend origin validation) head, diff, CI | Implemented but partially verified | Head `c923a11` read via git. Diff touches `.env.example`, `settings_validation.py`, its unit test, `docs/SECURITY.md`. CI runs not readable (no valid GitHub token). | Provide a token to read CI; review before any merge (approval-gated). |
| Check PR #20 (CI/CD hardening) head, diff, CI | Implemented but partially verified | Head `0574aa3` read via git. Deletes `.github/scripts/import_project_zip.py` (no references on `main`); pins three workflows. CI runs not readable. | Same as above. |
| Verify action SHA pins | Implemented but partially verified | PR #20 branch pins match upstream tag commits: `actions/checkout` v4 → `11d5960a…`, `actions/setup-python` v5 → `a26af69b…`, `actions/setup-node` v4 → `49933ea5…`, `aquasecurity/trivy-action` v0.36.0 → `ed142fd0…` (checked against the upstream repos). Not on `main` yet. | Merge is approval-gated. |
| Ensure no `pull_request_target` | Implemented and verified | `git grep pull_request_target` returns nothing. | None. |
| Ensure workflows have no write permissions | Implemented and verified | No `contents: write` or `id-token: write` in `.github`. | None. |

## Phase B: Security and database privileges

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Separate migration/admin role from runtime role | Implemented and verified | Compose: `migrate` (owner) → `provision-app-role` (one-shot) → `backend` as `logu_app`. CI `compose-smoke` run `39023269063` on commit `edf1d53`: success (Docker). | None for compose. |
| Runtime role is not superuser or owner | Implemented and verified | Test `test_02` and the provisioning script's own checks. Local PostgreSQL 16 (SQL level only). | None for SQL level. |
| Privilege tests (runtime cannot do admin actions) | Implemented and verified | `backend/tests/sql/test_runtime_role_privileges.py`: 11/11 pass on local PostgreSQL 16. Covers DDL, triggers, ownership, GRANT, role/database creation, `SET ROLE` membership, migration state, and that DML and append-only trigger still work. Full SQL suite 89/89 OK. | Run in CI (needs `COMMERCE_SQL_TEST_ADMIN_URI`). |
| Migration `20261009_0009` rollback/upgrade docs | Implemented but partially verified | `docs/DATABASE.md` and `docs/DEPLOYMENT.md` sections. Downgrade refused by design. Procedure not run against a live deployment. | Rehearse on staging before production. |

## Phase C: Encryption, secrets, application security

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Production secret validation (missing/weak/placeholder) | Implemented but partially verified | `backend/app/core/settings_validation.py`; unit tests pass locally (315 OK, Linux and Windows per user). | Re-run after any change. |
| Upload path absolute and outside source/web dirs | Implemented but partially verified | Validator plus Windows default fix in `config.py`. Unit tests pass. | None. |
| Encryption-key rotation procedure | Not implemented | No rotation code or procedure reviewed in this pass. | Design, then implement in an isolated environment. |
| CSP, security headers, CORS audit | Not implemented | Not audited in this pass. | Audit frontend and backend headers. |
| Rate limits, password reset, IDOR, mass-assignment audit | Not implemented | Not audited in this pass. | Audit with tests. |
| Dependency/container vulnerability scans | Implemented but partially verified | `container-security.yml` (Trivy), `project-import.yml` (pip-audit). Results not read. | Read latest run results (blocked). |

## Phase D to G: Backend, financial modules, bank

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Accounting, commerce, finance services and routes | Implemented but partially verified | Local: unit tests (315 OK), PostgreSQL rule tests (78 OK). Services and FastAPI routes not executed (deps not installable). | Install deps in CI or with registry access. |
| Idempotency for payments and settlements | Implemented but partially verified | DB uniqueness verified (0008 tests). Python replay path not executed. | Run integration tests. |
| Debt/EMI schedule math (Decimal) | Implemented and verified | Pure-rule unit tests. | None. |
| Tax and GST calculations | Not implemented | No GST module or verified official rules. Requires professional review and official-source check. | Requires my explicit approval for scope. |
| Bank read-only service and reconciliation | Implemented but partially verified | Read-only boundary tested statically; fixture provider only. | None until provider. |
| Live bank provider | Blocked by external dependency | No provider credentials or supported API. Not claimed. | Provide provider and consent flow. |
| Backup and restore | Not implemented | No backup script or restore procedure in repo. | Build scripts and rehearse on synthetic data. |

## Phase H to I: Notifications, OCR, AI

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| In-app notifications with dedupe | Implemented but partially verified | Dedupe via `ON CONFLICT`; PostgreSQL tests cover cycle. Runtime not executed. | Integration tests. |
| External email/SMS/push delivery | Blocked by external dependency | `NoExternalChannel` reports nothing delivered. | Provide provider credentials. |
| OCR/AI extraction | Blocked by external dependency | Abstraction and fixture only. | Provide provider credentials. |
| Document approval gate (no auto-post of uncertain data) | Implemented but partially verified | Low confidence and ambiguous codes require acknowledgement. Not executed end to end. | E2E test. |

## Phase J: Frontend

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Finance screens (14 pages) | Implemented but partially verified | Files exist. Syntax check passes. Typecheck and build NOT run (no `node_modules`). | Run `npm ci`, typecheck, build. |
| IntakeDetailView contract | Implemented but partially verified | Reads real job fields. Not run in browser. | Component tests. |
| Browser E2E tests | Not implemented | No browser test harness run. | Add after build works. |

## Phase K: Automated testing

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Backend unit tests | Implemented and verified | 315 OK locally (Linux), 315 OK on Windows (user report). | None. |
| PostgreSQL rule tests | Implemented and verified | 78 OK locally (Linux). | Re-run after changes. |
| Integration tests (`tests/integration`) | Implemented but partially verified | Written. Not executed (FastAPI, SQLAlchemy not installable). | Run in CI. |
| E2E Flows A-E | Implemented but partially verified | Written in `test_finance_flows.py`. Not executed. | Run in CI. |
| Frontend lint, typecheck, build | Implemented but partially verified | Config present. Not run here. | Run in CI. |

## Phase L to N: CI/CD, backup, dependencies

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Least-privilege workflow permissions | Implemented and verified | `contents: read` in all workflows. | None. |
| SHA-pinned third-party actions | Implemented but partially verified | PR #20 branch only (see Phase A). `main` still tag-pinned. | Merge is approval-gated. |
| Dependabot PR review | Blocked by external dependency | Config exists. PR list not readable. | Review once GitHub access works. |
| Backup scripts and restore rehearsal | Not implemented | Nothing in repo. | See Phase H-I backup row. |

## Phase O to R: Staging and production

| Task | Status | Evidence / blocker | Next action |
|---|---|---|---|
| Staging deployment | Blocked by external dependency | Needs hosting, domain, TLS, credentials. | Provide hosting and DNS details. |
| Production deployment | Requires my explicit approval | Not allowed without approval. | None. |
| Production readiness claim | Not implemented | Insufficient evidence. | Do not claim. |

## Approval-gated actions (do not perform without approval)

- Merging PR #19 or PR #20, or any PR.
- Pushing any branch to GitHub (repo currently attached read-only).
- DNS changes, staging or production deployment.
- Production database migration, encryption key replacement.
- Any paid resource or live provider billing.
- Major dependency upgrades.

## Known risks

- Compose switch verified by CI `compose-smoke` (run `39023269063`). Backend validation job `39024020732` on commit `b075f8f`: all steps success (unit, PostgreSQL integration, frontend lint/typecheck/build, compose config, image build).
- Action pins are only on PR #20's branch; `main` is tag-pinned (Phase L).
- Integration, E2E and frontend build results are unverified.
- Local copy and GitHub copy differ in many files (not reconciled).
