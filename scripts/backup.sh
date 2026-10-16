#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Usage: ./scripts/backup.sh [destination-directory]
# Run from the repository root with Docker Compose and the configured .env present.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env is missing. Copy .env.example and configure it first." >&2
  exit 2
fi
if ! docker compose config --quiet; then
  echo "ERROR: Docker Compose configuration is invalid." >&2
  exit 2
fi

DEST="${1:-$ROOT/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/logu-cfo-$STAMP"
mkdir -p "$OUT"
chmod 700 "$DEST" "$OUT"

cleanup() {
  if [[ "${KEEP_PARTIAL:-0}" != "1" ]]; then
    rm -f "$OUT/database.dump.partial" "$OUT/uploads.tar.gz.partial"
  fi
}
trap cleanup EXIT

echo "Checking database health..."
docker compose exec -T db pg_isready -U logu -d logu_cfo >/dev/null

echo "Creating PostgreSQL custom-format backup..."
docker compose exec -T db pg_dump -U logu -d logu_cfo --format=custom --no-owner > "$OUT/database.dump.partial"
test -s "$OUT/database.dump.partial"
mv "$OUT/database.dump.partial" "$OUT/database.dump"

echo "Creating upload-volume backup..."
docker compose exec -T backend tar -C /var/lib/logu-ai-cfo -czf - uploads > "$OUT/uploads.tar.gz.partial"
test -s "$OUT/uploads.tar.gz.partial"
tar -tzf "$OUT/uploads.tar.gz.partial" >/dev/null
mv "$OUT/uploads.tar.gz.partial" "$OUT/uploads.tar.gz"

(
  cd "$OUT"
  sha256sum database.dump uploads.tar.gz > SHA256SUMS
)
chmod 600 "$OUT"/*
printf 'Backup completed: %s\n' "$OUT"
printf 'Keep this directory encrypted and off-host. It contains business data.\n'
