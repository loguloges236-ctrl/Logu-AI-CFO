# Deployment

## Services (docker-compose.yml)

| Service | Image / build | Network | Notes |
|---|---|---|---|
| `db` | postgres:16-alpine | data (internal) | Not published |
| `clamav` | clamav/clamav:1.4 | data (internal) + updates (outbound egress only) | Not published; only ClamAV has external egress for signature refresh |
| `migrate` | backend image | data, app | Runs `alembic upgrade head`, then exits |
| `backend` | backend Dockerfile | app, data | Read-only root filesystem, `tmpfs /tmp`, `cap_drop: ALL`, non-root user |
| `frontend` | frontend Dockerfile | app | Standalone Next.js server, uid 1001 |
| `caddy` | caddy:2.8-alpine | app, public | Fixed IP 172.28.10.10; ports bound to `EDGE_BIND_ADDRESS` (default `127.0.0.1:8080` and `8443`) |

Networks: `data` and `app` are internal; `public` is the only route to the host, through Caddy.

## Configuration

Copy `.env.example` to `.env`. Required secrets (placeholders in the example):

- `DATABASE_URL` (PostgreSQL only)
- `DATA_ENCRYPTION_KEY` (Fernet key for document blobs and sensitive fields; losing it makes encrypted data unrecoverable)
- `SECURITY_HMAC_KEY` (required in staging/production)
- `POSTGRES_PASSWORD` (same password used by the Compose database and application URL)
- `TRUSTED_HOSTS`, `FRONTEND_ORIGINS`
- `TRUSTED_PROXY_NETWORKS` (set to `172.28.10.10/32` in compose)
- `BOOTSTRAP_TOKEN` (only while bootstrapping; disabled in production)

Production validation (`core/settings_validation.py`) refuses to start when a required secret is missing, a key is malformed, bootstrap is enabled, or docs are exposed.

## First start

1. `docker compose up -d db clamav`
2. `docker compose run --rm migrate` (or let `migrate` run with the stack)
3. Create the owner with the bootstrap flow, then disable bootstrap. Alternatively, set the owner password with `python -m app.cli reset-owner-password --email <address>` inside the backend container.
4. `docker compose up -d backend frontend caddy`

## Backup and restore

The repository includes guarded scripts for PostgreSQL and uploaded-document backups. These contain sensitive financial and document data. Store backup bundles encrypted, restrict access, and keep at least one copy off the server. Never commit backups or `.env` files.

1. Put the application into a maintenance window and stop user writes before backing up so database rows and upload blobs represent the same point in time. Keep the backend running for the upload-volume read step, but keep public traffic disabled at the edge.
2. Run `./scripts/backup.sh` from the repository root. It writes a timestamped directory under `backups/`, sets restrictive permissions, and creates SHA-256 checksums for `database.dump` and `uploads.tar.gz`.
3. Copy the whole backup directory to encrypted off-host storage. The script does not encrypt the bundle itself and does not copy `.env` or encryption keys.
4. To restore, first ensure the target server is in a maintenance window and you have a verified, separately secured copy of the original data and the matching `DATA_ENCRYPTION_KEY`. Run `./scripts/restore.sh /path/to/logu-cfo-YYYYMMDDTHHMMSSZ` and type `RESTORE` at the explicit confirmation prompt. This replaces the target database and overlays the upload volume. If the backend fails its health check, the script leaves frontend and Caddy stopped for investigation.
5. After restore, verify `/health/ready`, owner sign-in, document access/decryption, malware-scan state, ledger balances, and audit history before reopening traffic. A checksum verifies file integrity, not that a backup is logically complete; rehearse restores before relying on them.

## Operations

- Storage reconciliation: `python -m app.cli reconcile-storage` (dry run); add `--apply` to remove orphaned blobs older than one hour and mark stuck scans as failed.
- Password recovery: `reset-owner-password` (revokes all sessions and writes an audit row).
- Health: `/health` (process) and `/health/ready` (database `SELECT 1`, 503 on failure).

## Verification status (16 October 2026)

GitHub Actions successfully validates `docker compose config --quiet` and builds both the backend and frontend application images. The workflow also runs the Python unit suite, PostgreSQL integration suite, frontend install/lint/typecheck/production build, and dependency audits.

This confirms build and test behaviour on a clean CI runner. It does **not** confirm a live deployment. Before production, run the first-start procedure on the target host and verify Caddy DNS/TLS, the actual ClamAV service and signatures, persistent upload storage permissions, `/health/ready`, backup/restore, and owner bootstrap/recovery. Keep the example secrets out of any live deployment and do not expose internal backend/database ports.

## Future server migration

Nothing in the stack depends on AWS. Moving to an owned server requires the same PostgreSQL 16 instance, a persistent volume for `UPLOAD_STORAGE_PATH`, the same Fernet and HMAC keys, and the same Caddy routing.
