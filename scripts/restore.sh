#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Usage: ./scripts/restore.sh /absolute/path/to/logu-cfo-YYYYMMDDTHHMMSSZ
# Destructive: replaces the target database and overlays the upload volume.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 BACKUP_DIRECTORY" >&2
  exit 2
fi
BACKUP="$(cd "$1" && pwd)"
for file in database.dump uploads.tar.gz SHA256SUMS; do
  if [[ ! -s "$BACKUP/$file" ]]; then
    echo "ERROR: missing or empty backup file: $file" >&2
    exit 2
  fi
done
if ! (cd "$BACKUP" && sha256sum --check --status SHA256SUMS); then
  echo "ERROR: backup checksum verification failed." >&2
  exit 2
fi
# Reject absolute paths and traversal entries before extracting upload data.
if tar -tzf "$BACKUP/uploads.tar.gz" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "ERROR: upload archive contains an unsafe path." >&2
  exit 2
fi
if ! tar -tzf "$BACKUP/uploads.tar.gz" | awk '$0 == "uploads" || $0 ~ /^uploads\// { next } { bad = 1 } END { exit bad }'; then
  echo "ERROR: upload archive contains entries outside the uploads directory." >&2
  exit 2
fi
if [[ ! -f .env ]] || ! docker compose config --quiet; then
  echo "ERROR: .env missing or Docker Compose configuration invalid." >&2
  exit 2
fi

echo "WARNING: this replaces the current Logu AI CFO database and overlays stored uploads."
echo "Restore source: $BACKUP"
read -r -p 'Type RESTORE to continue: ' answer
if [[ "$answer" != "RESTORE" ]]; then
  echo "Restore cancelled."
  exit 1
fi

echo "Stopping public-facing services..."
docker compose stop caddy frontend backend || true

echo "Restoring PostgreSQL database..."
docker compose exec -T db pg_restore --clean --if-exists --no-owner --exit-on-error -U logu -d logu_cfo < "$BACKUP/database.dump"

echo "Starting backend privately to access the persistent upload volume..."
docker compose start backend
for attempt in $(seq 1 30); do
  if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)" >/dev/null 2>&1; then
  echo "ERROR: backend did not become healthy after database restore; frontend and Caddy remain stopped." >&2
  exit 1
fi

echo "Restoring upload files..."
docker compose exec -T backend tar -C /var/lib/logu-ai-cfo -xzf - < "$BACKUP/uploads.tar.gz"
echo "Starting frontend and edge proxy..."
docker compose start frontend caddy
printf 'Restore commands completed. Verify /health/ready, owner sign-in, document access, and audit records before resuming business use.\n'
