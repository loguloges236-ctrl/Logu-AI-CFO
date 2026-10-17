#!/bin/sh
# Container start script. Runs from WORKDIR /app.
#
# - RUN_MIGRATIONS=true: apply Alembic migrations first (used by the Render demo, which has no
#   separate migrate service). Local compose leaves this unset: the owner-role `migrate` service
#   runs migrations, and the backend runs as the least-privilege logu_app role, which cannot.
# - PORT: set by Render (10000). Defaults to 8000 for local compose.
set -eu

if [ "${RUN_MIGRATIONS:-}" = "true" ]; then
    alembic upgrade head
fi

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --no-server-header \
    --no-access-log
