# Deployment

## Services (docker-compose.yml)

| Service | Image / build | Network | Notes |
|---|---|---|---|
| `db` | postgres:16-alpine | data (internal) | Not published |
| `clamav` | clamav/clamav:1.4 | app (internal) | Not published; signature updates need outbound access |
| `migrate` | backend image | data, app | Runs `alembic upgrade head`, then exits |
| `backend` | backend Dockerfile | app, data | Read-only root filesystem, `tmpfs /tmp`, `cap_drop: ALL`, non-root user |
| `frontend` | frontend Dockerfile | app | Standalone Next.js server, uid 1001 |
| `caddy` | caddy:2.8-alpine | app, public | Fixed IP 172.28.10.10; ports bound to `EDGE_BIND_ADDRESS` (default `127.0.0.1:8080` and `8443`) |

Networks: `data` and `app` are internal; `public` is the only route to the host, through Caddy.

## Configuration

Copy `.env.example` to `.env`. Required secrets (placeholders in the example):

- `DATABASE_URL` (PostgreSQL only)
- `FIELD_ENCRYPTION_KEY` (Fernet key; validated at startup)
- `SECURITY_HMAC_KEY` (required in production)
- `TRUSTED_HOSTS`, `FRONTEND_ORIGINS`
- `TRUSTED_PROXY_NETWORKS` (set to `172.28.10.10/32` in compose)
- `BOOTSTRAP_TOKEN` (only while bootstrapping; disabled in production)

Production validation (`core/settings_validation.py`) refuses to start when a required secret is missing, a key is malformed, bootstrap is enabled, or docs are exposed.

## First start

1. `docker compose up -d db clamav`
2. `docker compose run --rm migrate` (or let `migrate` run with the stack)
3. Create the owner with the bootstrap flow, then disable bootstrap. Alternatively, set the owner password with `python -m app.cli reset-owner-password --email <address>` inside the backend container.
4. `docker compose up -d backend frontend caddy`

## Operations

- Storage reconciliation: `python -m app.cli reconcile-storage` (dry run); add `--apply` to remove orphaned blobs older than one hour and mark stuck scans as failed.
- Password recovery: `reset-owner-password` (revokes all sessions and writes an audit row).
- Health: `/health` (process) and `/health/ready` (database `SELECT 1`, 503 on failure).

## Verification status (16 October 2026)

GitHub Actions successfully validates `docker compose config --quiet` and builds both the backend and frontend application images. The workflow also runs the Python unit suite, PostgreSQL integration suite, frontend install/lint/typecheck/production build, and dependency audits.

This confirms build and test behaviour on a clean CI runner. It does **not** confirm a live deployment. Before production, run the first-start procedure on the target host and verify Caddy DNS/TLS, the actual ClamAV service and signatures, persistent upload storage permissions, `/health/ready`, backup/restore, and owner bootstrap/recovery. Keep the example secrets out of any live deployment and do not expose internal backend/database ports.

## Future server migration

Nothing in the stack depends on AWS. Moving to an owned server requires the same PostgreSQL 16 instance, a persistent volume for `UPLOAD_STORAGE_PATH`, the same Fernet and HMAC keys, and the same Caddy routing.
