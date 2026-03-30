#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, restart services
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv
PYTHON=/opt/python-3.14/bin/python3.14

# rsync code (preserve .venv, .env, data/)
# --chmod ensures files are world-readable so www-data (gunicorn) can read them
rsync -av --chmod=D755,F644 \
  --exclude '.venv' --exclude '.venv.*' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'data/' \
  "$REPO_ROOT/" "$TARGET_DIR/"

# Safety net: ensure world-readable in case of manual rsyncs without --chmod
find "$TARGET_DIR" -path "$TARGET_DIR/.venv" -prune -o -path "$TARGET_DIR/.venv.*" -prune -o -path "$TARGET_DIR/data" -prune -o -type f -exec chmod o+r {} \; -o -type d -exec chmod o+rx {} \;

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
