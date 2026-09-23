#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
: "${CLIENT_LOGIN:?Set CLIENT_LOGIN}"
: "${CLIENT_PASSWORD:?Set CLIENT_PASSWORD}"
: "${ADMIN_PASSWORD:?Set ADMIN_PASSWORD}"
# Accept simple demonstration passwords without dotenv quoting ambiguities.
case "$ADMIN_PASSWORD" in *[!a-zA-Z0-9_-]*) echo 'Use letters, digits, underscore or dash for ADMIN_PASSWORD' >&2; exit 1;; esac
test -f .env || { echo 'Create and configure .env first' >&2; exit 1; }
umask 077
env_tmp=$(mktemp .env.provision.XXXXXX)
trap 'rm -f "$env_tmp"' EXIT
awk '!/^[[:space:]]*ADMIN_PASSWORD[[:space:]]*=/' .env > "$env_tmp"
printf '\nADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD" >> "$env_tmp"
mv "$env_tmp" .env
docker compose up -d --build --wait --wait-timeout 180
docker compose exec -T -e CLIENT_LOGIN -e CLIENT_PASSWORD backend python -m backend.create_client
echo 'Admin password configured. Admin uses password-only login at /red-red/admin/catalog.'
