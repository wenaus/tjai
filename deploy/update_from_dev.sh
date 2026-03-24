#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, reload apache
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv

# rsync code (preserve .env in target)
rsync -av \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'data/' \
  "$REPO_ROOT/" "$TARGET_DIR/"

# Fix permissions for Apache (exclude .venv and data/ which have their own ownership)
find "$TARGET_DIR" -path "$TARGET_DIR/.venv" -prune -o -path "$TARGET_DIR/data" -prune -o -type f -exec chmod g+w,o+r {} \; -o -type d -exec chmod g+wx,o+rx {} \;

# Data dir: world-writable so both admin (nightly) and www-data (WSGI) can write
chmod -R a+rwX "$TARGET_DIR/data/" 2>/dev/null || true

# ensure env
if [[ ! -f $TARGET_DIR/.env ]]; then
  echo "WARNING: No .env file found. Copy from .env.example and configure."
  exit 1
fi

# create venv if missing
if [[ ! -d $VENV ]]; then
  python3 -m venv "$VENV"
fi

# install deps
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$TARGET_DIR/requirements/prod.txt"

# migrate and collect static files
pushd "$TARGET_DIR" >/dev/null
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput
popd >/dev/null

# reload apache
sudo systemctl reload apache2

echo "Deployment complete. Apache reloaded."
