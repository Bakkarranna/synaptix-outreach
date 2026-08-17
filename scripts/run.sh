#!/usr/bin/env bash
# scripts/run.sh — start the Synaptix Studio Console (Linux/macOS/WSL).
# Usage:  bash scripts/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYV=".venv/bin/python"
if [ ! -x "$PYV" ]; then
  echo "No virtualenv found — run bash scripts/setup.sh first." >&2
  exit 1
fi
mkdir -p data

alive() { kill -0 "$1" 2>/dev/null; }

# 1. Daemon — the outreach engine
if [ -f data/daemon.pid ] && alive "$(cat data/daemon.pid)"; then
  echo "Daemon already running (pid $(cat data/daemon.pid))."
else
  echo "Starting the outreach daemon…"
  nohup "$PYV" manage.py rundaemon --log-level info >>data/daemon.log 2>&1 &
  echo $! > data/daemon.pid
  echo "  daemon → pid $(cat data/daemon.pid)"
fi

# 2. Web server — the console UI
if [ -f data/web.pid ] && alive "$(cat data/web.pid)"; then
  echo "Web server already running (pid $(cat data/web.pid))."
else
  echo "Starting the web server…"
  nohup "$PYV" manage.py runserver 0.0.0.0:8000 --noreload >>data/web.log 2>&1 &
  echo $! > data/web.pid
  echo "  web → pid $(cat data/web.pid)"
fi

sleep 3
if command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8000/dashboard/ 2>/dev/null || true
elif command -v open >/dev/null 2>&1; then open http://localhost:8000/dashboard/ || true; fi

echo
echo "  Console:   http://localhost:8000/dashboard/"
echo "  Daemon:    data/daemon.log (dashboard shows its tail live)"
echo "  Web:       data/web.log"
echo "  Stop:      bash scripts/stop.sh"
echo
