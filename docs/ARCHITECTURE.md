# Architecture

## Components

| Component | Technology | Location |
|---|---|---|
| Edge reverse proxy | Caddy 2.8 (static IP 172.28.10.10 on the `app` network) | `docker/Caddyfile`, `docker-compose.yml` |
| Frontend | Next.js 15.3.4 App Router, React 19.1.0, TypeScript 5.8.3 strict | `frontend/` |
| Backend API | FastAPI 0.115.12 on uvicorn 0.34.2, Python 3.13 | `backend/app/` |
| Database | PostgreSQL 16 (only supported database) | `backend/app/db`, `backend/alembic` |
| Malware scanner | ClamAV 1.4 over clamd INSTREAM (TCP, fail-closed) | `backend/app/storage/clamav.py` |
| File storage | Local encrypted blob directory (`UPLOAD_STORAGE_PATH`) | `backend/app/storage/local.py` |

## Request path

1. Browser → Caddy (the only published service). Caddy forwards `/api/*` to the backend and everything else to the frontend.
2. The backend is reached only over the internal `app` network. The backend's `TRUSTED_PROXY_NETWORKS` is set to Caddy's fixed address, so only Caddy's `X-Forwarded-*` headers are believed.
3. `EdgeMiddleware` (pure ASGI, outermost) resolves the client IP, validates `Host` against `TRUSTED_HOSTS`, enforces HTTPS when `FORCE_HTTPS` is set (JSON 400, never a redirect, so there are no redirect loops), and adds security headers.
4. Route handlers use `deps.py` for the database session, the current authenticated session, CSRF checks, storage, cipher, and scanner.

## Frontend

- Route group `(app)/` holds the authenticated pages. Its layout is a **server component** that reads the session cookie via `cookies()` and calls the backend `/auth/me` through `API_INTERNAL_URL`. Without an owner it redirects to `/login`.
- `/login` redirects signed-in users to `/`.
- Browser calls use `lib/api.ts` (`apiFetch`): same-origin, CSRF header on state-changing methods, and a 401 response sends the user to `/login?expired=1`.
- `next.config.ts` sets the Content-Security-Policy and other headers for all routes. `unsafe-eval` is added in development only.
- No secret is placed in a `NEXT_PUBLIC_*` variable. Only the server-side `API_INTERNAL_URL` is used for server rendering.

## Backend modules

- `core/`: settings (`pydantic-settings`, validated in `settings_validation.py`), error envelope, clock, logging.
- `security/`: `proxy.py` (trusted-proxy resolution), `edge.py` (middleware), `keys.py` and `digester.py` (keyed HMAC-SHA256 for tokens and IP hashes), `crypto.py` (Fernet wrapper), `passwords.py` (Argon2).
- `models/entities.py`: SQLAlchemy 2.0 ORM mapping of the schema described in [DATABASE.md](DATABASE.md).
- `services/`: business logic (`auth`, `accounts`, `throttle`, `audit`, `documents`, `reconcile`) and `interfaces.py` (contracts only).
- `storage/`: scanner protocol and ClamAV client, file-type rules, local storage.
- `api/routes/`: HTTP layer for auth, documents, security status, and health.
- `cli.py`: operator commands (`reset-owner-password`, `reconcile-storage`).

## Interfaces without implementations (Phase 1)

- `AIProvider`, `OCRProvider`: contracts only. No model or OCR engine is called.
- `NotificationProvider`: `DisabledNotificationProvider` is the only implementation, so password-reset tokens are never issued through email; the operator CLI is the recovery path.
- `SpeakerVerificationProvider`, `AntiSpoofingProvider`: contracts only. **Voice unlock is not implemented.** The API reports `implemented: false` and the enrolment endpoint returns 501.

## Phase 2: core accounting

- `backend/app/accounting/` is pure Python (no framework imports): `domain.py` (money, balancing, normal balances, TB/P&L/BS maths, ledger running balance), `default_chart.py` (generic template), `ai_boundary.py` (read-only tool names).
- `backend/app/services/accounting.py` applies the domain rules through SQLAlchemy, commits explicitly, and writes audit rows. Posting takes row locks (entry `FOR UPDATE`, period `FOR SHARE`); closing a period takes `FOR UPDATE`, so a period cannot close during a posting.
- `backend/app/api/routes/accounting.py` exposes the authenticated API; mutations require the CSRF token. Rule failures are translated by `_translate()` into stable codes (404, 409 or 422).
- The frontend accounting screens live under `frontend/app/(app)/accounting/` and share helpers in `frontend/components/accounting/`. They use integer paise for totals and never convert money to floats.
- See [ACCOUNTING.md](ACCOUNTING.md) for the rules and API list.

## Phase 3: commerce

- `app/commerce/`: pure rules for money, quantities, stock decisions, workflow transitions, account roles and document extraction. No database access.
- `app/services/commerce_*.py`: catalogue, pricing, inventory, purchasing, sales, document processing and reports. Each business event runs in one unit of work and posts through the Phase 2 accounting service with `commit=False`.
- `app/api/routes/commerce.py`: authenticated routes. Reads need a session; writes also need the CSRF token. Domain and accounting rule failures map to stable error codes.
- `app/commerce/extraction.py`: the document-processing provider interface. The default provider reports that none is connected.
- `frontend/components/commerce/` and `frontend/app/(app)/commerce/`: mobile-first screens over the same API.

The AI layer is read-only with respect to the database and sends proposals through the same services as people. See [COMMERCE.md](COMMERCE.md#ai-boundary).
