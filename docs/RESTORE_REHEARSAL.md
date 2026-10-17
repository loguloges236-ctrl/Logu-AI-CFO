# Backup and restore rehearsal

Status: **Not yet rehearsed.** The scripts exist (`scripts/backup.sh`, `scripts/restore.sh`) and are syntax-checked by CI (`Validate operational shell scripts`). A real restore has not been run against a database holding data. Do this on a non-production host before any production data is trusted.

## What a rehearsal must prove

1. The backup bundle is complete: `database.dump` and `uploads.tar.gz` both exist and their SHA-256 checksums verify.
2. The restore replaces the target database and overlays the upload volume without errors.
3. The restored application starts, `/health/ready` returns 200, and the owner can sign in.
4. A sample document downloads and decrypts with the matching `DATA_ENCRYPTION_KEY`.
5. Ledger balances and audit history match the values recorded before the backup.
6. The timing is recorded (backup duration, restore duration, data size) to set the recovery-time target.

## Procedure (non-production host, disposable data)

1. Start the stack from a clean state with the same image version as production (`docker compose up -d --build`, or the chosen non-Docker deployment).
2. Load test data: create an owner, a few accounts, a journal entry, an invoice, one uploaded document. Record ledger totals, the audit log count, and the document sha256.
3. Put the application in maintenance: stop frontend and Caddy so no user writes arrive. Keep the backend running for the upload read.
4. `./scripts/backup.sh`. Note the bundle path. Check `sha256sum -c` on both files.
5. Copy the bundle to a second location to simulate off-host storage.
6. Destroy the rehearsal database and upload volume (rehearsal host only, never production).
7. `./scripts/restore.sh <bundle-path>` and type `RESTORE` at the prompt.
8. Verify items 3 to 5 from the list above against the values recorded in step 2.
9. Record the outcome in PROJECT_COMPLETION.md with the date, the bundle checksums, and the timings.

## Failure handling

- If `restore.sh` leaves frontend and Caddy stopped, investigate the backend health check before reopening traffic.
- If a checksum fails, do not restore. Obtain another copy of the bundle.
- Never run a restore against production without an approved change and a verified backup of the current state.

## Scope limits

- The checksum proves file integrity, not logical completeness. The verification list above is the logical check.
- Encryption keys are not in the bundle. A restore needs the matching `DATA_ENCRYPTION_KEY` (and any retired keys, see docs/KEY_ROTATION.md).
