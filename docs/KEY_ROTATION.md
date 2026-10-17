# Data encryption key rotation

Status legend follows PROJECT_COMPLETION.md: **Implemented and verified**, **Implemented but partially verified**, **Not implemented**, **Requires explicit approval**.

## What is protected

- Uploaded document bytes (`document_files`, stored under `UPLOAD_STORAGE_PATH`), encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
- Sensitive fields encrypted by the application through `SensitiveDataCipher`.

Keys: `DATA_ENCRYPTION_KEY` (primary, required in staging and production) and `DATA_ENCRYPTION_KEY_PREVIOUS` (optional, comma-separated retired keys).

## Current behaviour (Implemented and verified at unit level)

- New encryptions always use the primary key.
- Decryption tries the primary key, then each previous key (`MultiFernet`). Data written under a retired key stays readable while that key is listed.
- `rotate_bytes(token)` re-encrypts a token under the primary key. It fails with a safe error if no configured key can verify the token.
- `is_current(token)` tells whether a token already uses the primary key.
- Settings validation rejects malformed previous keys.
- Tests: `backend/tests/unit/test_crypto_edge.py` (`KeyRotationTests`, 5 tests, plus the existing cipher tests). 19 tests pass in the authoring environment.

## What is NOT implemented

- **Re-encryption job.** No command walks `document_files` and rewrites stored blobs. Until it exists, retired keys must stay in `DATA_ENCRYPTION_KEY_PREVIOUS` for as long as any old blob exists. **Not implemented.**
- **Key-version tracking.** Tokens do not record which key encrypted them. Finding remaining old blobs means trying keys, or using `is_current`. **Not implemented.**
- **Production rotation.** Replacing the production key is approval-gated. **Requires explicit approval.**

## Procedure once the re-encryption job exists (planned, not yet runnable)

1. Back up the database and `UPLOAD_STORAGE_PATH` (see docs/DEPLOYMENT.md). Keep the old key in a separate secure store.
2. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Never commit it or print it into logs.
3. Set `DATA_ENCRYPTION_KEY` to the new key and `DATA_ENCRYPTION_KEY_PREVIOUS` to the old key. Restart the backend. Reads and writes now work for both.
4. Run the re-encryption job in dry-run mode, then apply. It must be idempotent and must write each blob atomically (temp file, then rename), and must verify the sha256 of the plaintext after rewriting.
5. Confirm the job reports zero blobs that fail `is_current`, then remove the old key from `DATA_ENCRYPTION_KEY_PREVIOUS`.
6. Keep the old key offline for the agreed retention period, in case a restore from a pre-rotation backup is needed.

## Risks

- Losing every key that can decrypt existing blobs makes them permanently unreadable. Keep the previous key until verification passes.
- A partially rewritten set is safe only if each blob is written atomically and each read tries all configured keys.
