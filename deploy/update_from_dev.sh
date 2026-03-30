#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, restart services
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv
PYTHON=/opt/python-3.14/bin/python3.14

# rsync code (preserve .venv, .env, data/)
rsync -av \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'data/' \
  "$REPO_ROOT/" "$TARGET_DIR/"

# Fix permissions (exclude .venv and data/ which have their own ownership)
find "$TARGET_DIR" -path "$TARGET_DIR/.venv" -prune -o -path "$TARGET_DIR/data" -prune -o -type f -exec chmod g+w,o+r {} \; -o -type d -exec chmod g+wx,o+rx {} \;

# Data dir: world-writable so both admin and www-data can write
chmod -R a+rwX "$TARGET_DIR/data/" 2>/dev/null || true

# ensure env
if [[ ! -f $TARGET_DIR/.env ]]; then
  echo "WARNING: No .env file found. Copy from .env.example and configure."
  exit 1
fi

# create venv if missing
if [[ ! -d $VENV ]]; then
  $PYTHON -m venv "$VENV"
fi

# install deps
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$TARGET_DIR/requirements/prod.txt"

# migrate and collect static files
pushd "$TARGET_DIR" >/dev/null
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput
popd >/dev/null

# restart services
sudo systemctl restart tjai-gunicorn
sudo systemctl restart tjai-tgbot
/var/www/tjai/.venv/bin/supervisorctl -c /var/www/tjai/deploy/supervisord.conf restart action-agent

echo "Deployment complete."
