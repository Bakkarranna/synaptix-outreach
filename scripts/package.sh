#!/usr/bin/env bash
# scripts/package.sh - build a distributable bundle of the Console.
# Usage:
#   bash scripts/package.sh                  # secrets stay OUT of the bundle
#   bash scripts/package.sh --with-config    # include deploy/onboard_config.json
#                                            # (for the one-shot VM installer)
#
# With --with-config the bundle carries the real credentials from
# deploy/onboard_config.json - ONLY use this for a server you own, and never
# share that bundle with anyone. Without it, .env / config are excluded.
set -euo pipefail
cd "$(dirname "$0")/.."

include_config=0
[ "${1:-}" = "--with-config" ] && include_config=1

stamp=$(date +%Y%m%d-%H%M)
mkdir -p dist
out="dist/synaptix-studio-console-$stamp.tar.gz"

config_exclude=()
if [ "$include_config" = "1" ]; then
  echo "  WARNING: bundling deploy/onboard_config.json (live credentials)."
  echo "           Only for your own server. Never share this bundle."
else
  config_exclude+=(--exclude=deploy/onboard_config.json)
fi

tar -czf "$out" \
  --exclude=.venv --exclude=.git --exclude=data --exclude=media \
  --exclude=staticfiles --exclude=.pytest_cache --exclude=.cache \
  --exclude=dist --exclude='__pycache__' --exclude='*.pyc' \
  --exclude=.env --exclude=deploy/backups \
  "${config_exclude[@]}" .

size=$(du -h "$out" | cut -f1)
echo
echo "  Packaged -> $out ($size)"
if [ "$include_config" = "1" ]; then
  echo "  Server: scp this bundle to the VM, then run deploy/install-on-vm.sh"
else
  echo "  Recipient: extract -> bash scripts/setup.sh -> bash scripts/run.sh"
fi
echo
