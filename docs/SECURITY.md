# Security model (Phase 1)

This document describes what the code does. Items marked **limitation** are known gaps.

## Authentication

- Single owner. Passwords are hashed with Argon2 (`argon2-cffi`). Length is 14–256 characters for new passwords; sign-in accepts any length so existing hashes remain usable.
- Unknown e-mail addresses run a dummy Argon2 verification (`burn_verification_time`) so response timing does not reveal whether an account exists.
- Login, reset-request, reset-confirm and password-change responses are generic and do not reveal whether an account exists.
- Throttling (`services/throttle.py`) is database-backed, sliding-window, and bounded (failure rows are purged after one day). It limits attempts per IP, per e-mail, and per combined scope. Limits are temporary (`429` with `Retry-After`); **the account is never permanently locked**, which prevents a denial-of-service lockout of the owner.
- Bootstrap (creating the first owner) is disabled unless explicitly enabled, requires a token compared in constant time, and is refused in production.

## Sessions and devices

- Sessions are opaque random tokens; only a keyed HMAC of the token is stored (`sessions.token_hash`).
- Absolute expiry and idle timeout (`SESSION_IDLE_MINUTES`, default 60). Activity updates `last_seen_at` at most once every 60 seconds.
- A device-binding cookie (400 days) identifies the device. Revoking a device revokes its sessions.
- Users can list and revoke their sessions, sign out other sessions, and list and revoke devices.
- Changing the password keeps the current session and revokes the others. Completing a reset revokes all sessions.

## CSRF and origin checks

- Double-submit token: a readable CSRF cookie plus a matching header, with the value bound to the session by HMAC.
- `Origin` is checked on state-changing requests (`403 origin_not_allowed`).
- Session cookies are `HttpOnly`; the CSRF cookie is readable by the frontend.

## Proxy and HTTPS

- `X-Forwarded-For` is resolved right to left, and only hops that come from `TRUSTED_PROXY_NETWORKS` are believed. Spoofed leftmost entries are ignored.
- `X-Forwarded-Proto` is trusted only from trusted proxies. A request that arrives from an untrusted peer with `X-Forwarded-Proto: https` is not treated as secure.
- When `FORCE_HTTPS` is set, plain-HTTP requests receive a JSON 400 (`https_required`). The middleware never redirects.
- `TRUSTED_HOSTS` is validated at startup and enforced per request (`invalid_host`).
- `INTERNAL_API_HOSTS` (compose: `backend`) lets the frontend container call the API directly on the private network for server-side rendering. A request whose Host is in this list is accepted only when its TCP peer is NOT a trusted proxy. Through Caddy, the same Host is rejected. The setting is valid only while the backend has no published port, which is the compose default.
- Server-side calls from the frontend carry no forwarded headers, so `FORCE_HTTPS` does not apply to them. Requests through Caddy still must present `X-Forwarded-Proto: https`.

## Rate-limit and IP privacy

- IP addresses are stored only as keyed HMAC digests (`SECURITY_HMAC_KEY`, domain-separated per purpose).
- If `SECURITY_HMAC_KEY` is unset, an ephemeral key is generated with a warning. This is acceptable only for development; production validation requires the key.

## API surface

- OpenAPI/Swagger documentation is served only when the environment is `development`.
- CORS is enabled only when `FRONTEND_ORIGINS` is set.
- Validation errors do not echo submitted values. Unhandled exceptions return a generic 500.
- Access logging is disabled (**limitation**: operational logs contain fewer request details).

## Documents

- Intake: content type is decided by magic bytes, not by the client-supplied type. Allowed: PDF, JPEG, PNG, WEBP. Maximum size is enforced (413). Unsupported types return 415; malformed content returns 422.
- Scan states: `PENDING → SCANNING → CLEAN | INFECTED | FAILED`. Each transition is committed durably. A file is **never** `CLEAN` without a completed ClamAV scan.
- ClamAV unavailable or an unparseable reply → `FAILED` with 503. There is no development bypass.
- `INFECTED` → document `QUARANTINED`, 422, and the blob is not stored.
- Only `CLEAN` files are encrypted and stored. Downloads require `CLEAN`, decrypt the blob, check the SHA-256 of the plaintext, and write a `document_downloaded` audit row. A mismatch returns a 500 integrity error and is audited.
- Stored file names are random hex keys. Display names are sanitised, and `Content-Disposition` is built with safe encoding.
- Executable file types are rejected at intake.
- Deletion revokes the document and removes blobs after commit. Orphaned blobs are reported and removed by `reconcile-storage` after a one-hour grace period. Files stuck in `SCANNING` are marked `FAILED`.

## Encryption at rest

- Fernet (AES-128-CBC with HMAC-SHA256) via `SensitiveDataCipher`, keyed by `FIELD_ENCRYPTION_KEY`. The same key is used for document blobs and sensitive text fields.
- Key validation rejects malformed keys at startup.
- **Limitation:** one key protects both fields and files, and there is no key rotation procedure yet.

## Audit and security events

- `audit_logs` and `security_events` are append-only. PostgreSQL triggers reject `UPDATE`, `DELETE` and `TRUNCATE` on both tables.
- Metadata passes through `safe_metadata`, which drops keys containing password, token, authorization, cookie, CSRF, secret, API key, key, or signature.
- **Limitation:** a database superuser can still drop triggers. Protection against that requires database-level role separation (see the roadmap).

## Frontend

- Server-side guard on the `(app)/` layout; client calls use same-origin requests.
- Content-Security-Policy restricts sources. Scripts still permit `'unsafe-inline'` because Next.js inline bootstrapping requires it (**limitation**; a nonce-based policy is planned).
- `robots` noindex metadata. No third-party fonts or analytics.
- Secrets are not exposed through `NEXT_PUBLIC_*`.

## Voice unlock

**Not implemented.** There is no biometric matching, enrolment, or anti-spoofing. The API states this explicitly. No code path accepts a voice sample as authentication.

## Known security limitations (remaining at end of Phase 1)

1. Internal traffic between Caddy, frontend, and backend is plain HTTP on private Docker networks.
2. A single encryption key covers fields and files; no rotation.
3. No password reset delivery channel; recovery is operator-only.
4. Database superuser can bypass audit triggers.
5. CSP keeps `'unsafe-inline'` for scripts.
6. Access logs are disabled.
7. Python runtime dependency audit and production npm dependency audit pass in CI. The full npm development dependency tree still reports high-severity GHSA-vfj7-8cjw-p6xm for `braces`, pulled transitively through the ESLint/Next lint toolchain. As of 16 October 2026 the advisory lists no patched upstream release. CI therefore gates `npm audit --omit=dev --audit-level=high` and documents this development-tooling limitation rather than applying a risky major downgrade. Trivy/container vulnerability scanning is not yet part of CI.
8. `frontend/package-lock.json` is committed; `npm ci`, lint, typecheck, and production build pass in CI.
9. Cross-owner authorisation tests are not meaningful under the single-owner schema.

## Phase 2: accounting controls

- Every accounting route requires the owner session; every mutation also requires the CSRF token. There are no public accounting routes and no endpoint accepts SQL or unrestricted filters.
- Money is accepted only as a decimal string. JSON numbers are rejected (`422`), so binary floats never enter the flow.
- Posted accounting history is protected by database triggers, not only by the API. Posted entries and lines cannot be changed or deleted; accounts with postings cannot change code or category; periods cannot be deleted or reopened.
- Posting and period closing are serialised by row locks, so a period cannot be closed between the open-period check and the posting commit.
- The AI boundary is read-only by construction (`ai_boundary.py`). A test refuses any tool name that implies a write or raw query.
- All accounting mutations are audited. Metadata passes through the same filter as Phase 1 (no secret-like keys).

## Phase 3: commerce controls

- Every commerce route requires an authenticated owner session; every state-changing route also requires the CSRF token. A static test enforces this over the route module.
- No raw SQL is executed in routes, and no route accepts filter expressions.
- Stock and posted-document history are append-only, enforced by database triggers (including against TRUNCATE). Voiding is by reversal, never deletion.
- Accounting entries are created only through the Phase 2 service. Roles map to accounts the owner chooses; the AI layer cannot post.
- Document approval creates draft documents only. It cannot change stock or the ledger.
- Payment references are stored for audit. No payment is executed.

Not yet covered: an access log for commerce reads, and a review of the document-processing provider's data handling once a real provider is connected.

## Dependency audit status (16 October 2026)

- `pip-audit -r backend/requirements.txt`: passing after updating FastAPI, cryptography, and python-multipart to patched dependency versions.
- `npm audit --omit=dev --audit-level=high`: passing after upgrading Next.js/React and overriding vulnerable transitive production packages `postcss` and `sharp`.
- Full `npm audit` still reports the development-only `braces` advisory GHSA-vfj7-8cjw-p6xm through `eslint-config-next` tooling. The advisory database currently lists no patched upstream version (affected through 3.0.3). It is not reported as fixed; re-check upstream before future dependency updates. Do not use `npm audit fix --force` because npm proposes unrelated breaking Next/ESLint major downgrades.
