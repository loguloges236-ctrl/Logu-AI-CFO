# Render temporary demo (sample data only)

**Status: draft. Not deployed. Not verified on Render.** Use it only for a short demonstration. Nothing here is production.

## Limits that matter (from Render's free-tier docs)

- Web services sleep after 15 minutes without traffic; the first request after that takes about a minute.
- The free PostgreSQL database expires 30 days after creation, and its data is deleted after a 14-day grace period. No backups.
- No persistent disk. Files written to the container are lost on restart. ClamAV is not deployed, so uploads fail closed (rejected, not stored). This is intentional.
- Free instances have a monthly instance-hour allowance, and no private services, so ClamAV cannot run here.

## Use only sample data

Logu handles financial and personal records. Do not enter real customers, invoices, bank details, or real owner credentials into this demo.

## Steps (after you approve)

1. Create a Render account (sign in with GitHub). Check at signup whether a card is requested; if it is, stop and tell the owner.
2. Connect the GitHub repository. The branch must be the one you approve (currently `feature/db-runtime-role`). Do not deploy `main` unless it contains the reviewed changes.
3. In Render, apply the Blueprint (`render.yaml`). It creates `logu-backend`, `logu-frontend`, and `logu-db`.
4. Fill in the secrets in the dashboard:
   - `DATABASE_URL`: copy the internal connection string from `logu-db` and change the scheme to `postgresql+psycopg://`. Render's default is `postgresql://`, which this app rejects.
   - `SECURITY_HMAC_KEY`: a random string of at least 32 characters (generate locally: `python -c "import secrets; print(secrets.token_hex(32))"`).
   - `DATA_ENCRYPTION_KEY`: a Fernet key (generate locally: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Keep a copy only in your password manager.
   - `TRUSTED_HOSTS` and `FRONTEND_ORIGINS`: the exact public hostnames Render assigns (shown after the first deploy). Do not use `*`.
   - `API_INTERNAL_URL` on the frontend: the backend's public `https://` URL.
5. Redeploy both services, then check `https://<backend>/health` (expect 200) and open the frontend.

## Known risks to verify on the first deploy (not yet tested)

- **Proxy headers and HTTPS:** Render terminates TLS before the container. With `FORCE_HTTPS=true`, the app may see plain HTTP from Render's proxy and reject requests. Check the first login. If it fails, the fix is to set `TRUSTED_PROXY_NETWORKS` to Render's proxy range, which must be confirmed from Render's documentation, not guessed.
- **Cookies across subdomains:** The frontend and backend are on different `onrender.com` subdomains. Browser cookies (`SameSite=lax`) may not be sent by the browser to the backend. Check that login stays signed in.
- **Migrations on every start:** `alembic upgrade head` runs at each container start. It is idempotent, but it runs as the database owner. Acceptable for a demo only.
- **Cold start:** the first request after sleep may time out on the frontend.

## Teardown

Delete the three Render services when the demo ends. Deleting `logu-db` removes all demo data.
