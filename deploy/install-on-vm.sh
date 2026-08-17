#!/usr/bin/env bash
# deploy/install-on-vm.sh - one-shot install of the Synaptix Studio Console.
#
# Run on the freshly created Oracle VM, from the extracted project dir:
#   1. upload the bundle from the studio machine:
#        scp -i ~/.ssh/<key> dist/synaptix-studio-console-*.tar.gz ubuntu@<IP>:~
#   2. extract and run (the installer ships inside the bundle):
#        mkdir -p ~/console && tar xzf synaptix-studio-console-*.tar.gz -C ~/console
#        cd ~/console && bash deploy/install-on-vm.sh
#
# It installs Docker, writes .env (it asks you only for the server's public
# IP), starts web + daemon, and shows the first-boot log. The onboarding
# config deploy/onboard_config.json travels inside the bundle (package with
# `bash scripts/package.sh --with-config`) and is applied automatically - you
# do not type a single secret on the VM.
set -euo pipefail

DOCKER() { # plain docker if it works, sudo docker otherwise
  if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi
}

echo
echo "  Synaptix Studio Console - server install"
echo "  ----------------------------------------"
echo "  Project at: $(pwd)"
echo

# ── Docker (idempotent) ──────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  echo "  Installing Docker (this takes a minute)..."
  wget -qO- https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "  Docker installed."
else
  echo "  Docker already installed."
fi

# ── .env (the only thing you type: the public IP) ────────────────────
if [ ! -f .env ]; then
  echo
  read -r -p "  Public IP of this server (from the Oracle console) - or press Enter to allow any: " PUBLIC_IP
  SECRET="$(tr -dc 'A-Za-z0-9_-' </dev/urandom | head -c 50)"
  cat > .env <<EOF
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=${PUBLIC_IP:-*}
DJANGO_CSRF_TRUSTED_ORIGINS=
HOST_UID=1000
HOST_GID=1000
EOF
  echo "  .env written (hosts: ${PUBLIC_IP:-*})."
else
  echo "  .env already present - keeping it."
fi

# ── Onboarding config present? ───────────────────────────────────────
if [ ! -f deploy/onboard_config.json ]; then
  echo
  echo "  NOTE: deploy/onboard_config.json is missing. The daemon will fall back"
  echo "  to the interactive wizard (attach to it with 'docker compose logs -f daemon')."
  echo "  Rebuild the bundle with 'bash scripts/package.sh --with-config' to ship it."
fi

# ── First boot ───────────────────────────────────────────────────────
echo
echo "  Building and starting web + daemon (first build takes a few minutes)..."
DOCKER compose -f compose.prod.yml up -d --build

echo
echo "  First-boot log (Ctrl+C to stop following - the stack keeps running):"
DOCKER compose -f compose.prod.yml logs --tail=60 -f daemon

echo
echo "  Done. Open  http://<server-ip>:8000/dashboard/  once the log shows"
echo "  'missing steps: NONE'."
echo "  Sign in with the account email + password stored in"
echo "  deploy/onboard_config.json on the studio machine (change the password"
echo "  later in the dashboard's Team page)."
echo "  Day-to-day:  cd ~/console && bash deploy/backup.sh   /   bash deploy/update.sh"
echo "  Tip: log out and back in once so your user can run docker without sudo."
