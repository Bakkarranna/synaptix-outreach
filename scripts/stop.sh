#!/usr/bin/env bash
# scripts/stop.sh — stop the daemon and the web server.
cd "$(dirname "$0")/.."

for name in daemon web; do
  pidfile="data/$name.pid"
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Stopped $name (pid $pid)"
    else
      echo "$name was not running (stale pidfile removed)"
    fi
    rm -f "$pidfile"
  fi
done
echo "All stopped. Pipeline state is safe — the daemon resumes where it left off."
