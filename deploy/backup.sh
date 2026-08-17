#!/usr/bin/env bash
# deploy/backup.sh — consistent snapshot of the Console's whole store.
#
# The entire store (leads, deals, conversations, model blobs, config) is one
# SQLite file on the shared oo-data volume. We snapshot it through sqlite3's
# backup API (safe even while the daemon is writing), then stream it out of the
# running container — no volume-name guessing.
#
# Usage:  bash deploy/backup.sh            # into deploy/backups/
#         bash deploy/backup.sh /path/dir  # into a custom directory
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER() { # plain docker if it works, sudo docker otherwise
  if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi
}

DEST="${1:-deploy/backups}"
mkdir -p "$DEST"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/db-$TS.sqlite3"

# Prefer the web container (usually up first); fall back to the daemon.
SERVICE="web"
if ! DOCKER compose -f compose.prod.yml ps --status running "$SERVICE" | grep -q running; then
  SERVICE="daemon"
fi

DOCKER compose -f compose.prod.yml exec -T "$SERVICE" python -c "
import sqlite3
src = sqlite3.connect('data/db.sqlite3')
dst = sqlite3.connect('/tmp/db-backup.sqlite3')
src.backup(dst)
dst.close(); src.close()
print('snapshot ok')
"
DOCKER compose -f compose.prod.yml cp "$SERVICE:/tmp/db-backup.sqlite3" "$OUT"
DOCKER compose -f compose.prod.yml exec -T "$SERVICE" rm -f /tmp/db-backup.sqlite3

size=$(du -h "$OUT" | cut -f1)
echo "  Backup -> $OUT ($size)"
echo "  Restore: stop the stack, replace data/db.sqlite3 on the volume, start again."
