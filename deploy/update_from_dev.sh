#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, reload apache
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv

# rsync code (preserve .env in target)
rsync -av --delete \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' \
  "$REPO_ROOT/" "$TARGET_DIR/"

# Fix permissions for Apache (exclude .venv which has different ownership)
find "$TARGET_DIR" -path "$TARGET_DIR/.venv" -prune -o -type f -exec chmod o+r {} \; -o -type d -exec chmod o+rx {} \;

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

# migrate
pushd "$TARGET_DIR" >/dev/null
"$VENV/bin/python" manage.py migrate --noinput
popd >/dev/null

# reload apache
sudo systemctl reload apache2

echo "Deployment complete."
