#!/usr/bin/env bash
# deploy/update.sh — pull the latest code and rebuild the stack in place.
#
# Assumes the app on the VM is a git checkout (Option B in deploy/README.md).
# If you shipped it as a bundle instead, re-copy the new bundle and unpack it
# over the old tree, then run:  docker compose -f compose.prod.yml up -d --build
#
# Usage:  bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER() { # plain docker if it works, sudo docker otherwise
  if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi
}

if [ -d .git ]; then
  git pull --ff-only
else
  echo "  No .git here - re-copy the latest bundle over this tree first."
fi

DOCKER compose -f compose.prod.yml up -d --build
echo "  Stack rebuilt. Following the daemon log (Ctrl+C to stop following):"
DOCKER compose -f compose.prod.yml logs --tail=80 -f daemon
