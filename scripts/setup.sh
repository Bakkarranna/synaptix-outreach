#!/usr/bin/env bash
# scripts/setup.sh — Synaptix Studio Console, first-time setup (Linux/macOS/WSL).
# Usage:  bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo
echo "  Synaptix Studio Console — setup"
echo "  Distinction in Design. Discipline in Code."
echo

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.12+ is required and must be on PATH." >&2
  exit 1
fi

# 2. Virtual environment
PYV=".venv/bin/python"
if [ ! -x "$PYV" ]; then
  echo "[1/5] Creating virtual environment…"
  python3 -m venv .venv
fi

# 3. Dependencies
echo "[2/5] Installing dependencies…"
"$PYV" -m pip install --upgrade pip --quiet
"$PYV" -m pip install -r requirements/local.txt --quiet

# 4. Database + CRM
echo "[3/5] Migrating database…"
"$PYV" manage.py migrate --no-input
echo "[4/5] Bootstrapping CRM…"
"$PYV" manage.py setup_crm

# 5. Staff account (the Lead)
STAFF=$("$PYV" manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.filter(is_staff=True, is_active=True).count())" | tail -1)
if [ "${STAFF:-0}" = "0" ]; then
  echo "[5/5] No staff account yet — create the Lead account."
  "$PYV" manage.py createsuperuser
else
  echo "[5/5] Staff account already exists — skipping."
fi

echo
echo "  Setup complete."
echo "  Start the Console:   bash scripts/run.sh"
echo "  Then open:           http://localhost:8000/dashboard/"
echo
