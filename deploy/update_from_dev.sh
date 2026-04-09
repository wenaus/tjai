#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, restart services
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv
PYTHON=/opt/python-3.14/bin/python3.14
SECONDS=0

# rsync code (preserve .venv, .env, data/)
# --chmod ensures files are world-readable so www-data (gunicorn) can read them
rsync -a --chmod=D755,F644 \
  --exclude '.venv' --exclude '.venv.*' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'data/' \
  "$REPO_ROOT/" "$TARGET_DIR/"
echo "[${SECONDS}s] rsync done"

# ensure env
if [[ ! -f $TARGET_DIR/.env ]]; then
  echo "WARNING: No .env file found. Copy from .env.example and configure."
  exit 1
fi

# create venv if missing
if [[ ! -d $VENV ]]; then
  $PYTHON -m venv "$VENV"
fi

# install deps only if requirements changed
REQ_FILE="$TARGET_DIR/requirements/prod.txt"
REQ_HASH_FILE="$TARGET_DIR/.last_requirements_hash"
REQ_HASH=$(md5sum "$REQ_FILE" | cut -d' ' -f1)
if [[ ! -f "$REQ_HASH_FILE" ]] || [[ "$(cat "$REQ_HASH_FILE")" != "$REQ_HASH" ]]; then
  "$VENV/bin/pip" install -r "$REQ_FILE"
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "Requirements unchanged, skipping pip install."
fi
echo "[${SECONDS}s] pip done"

# migrate
pushd "$TARGET_DIR" >/dev/null
"$VENV/bin/python" manage.py migrate --noinput
echo "[${SECONDS}s] migrate done"

# collect static only if static files changed
STATIC_HASH=$(find "$TARGET_DIR/tjai_app/static" -type f -exec md5sum {} \; 2>/dev/null | sort | md5sum | cut -d' ' -f1)
STATIC_HASH_FILE="$TARGET_DIR/.last_static_hash"
if [[ ! -f "$STATIC_HASH_FILE" ]] || [[ "$(cat "$STATIC_HASH_FILE")" != "$STATIC_HASH" ]]; then
  "$VENV/bin/python" manage.py collectstatic --noinput
  echo "$STATIC_HASH" > "$STATIC_HASH_FILE"
else
  echo "Static files unchanged, skipping collectstatic."
fi
echo "[${SECONDS}s] static done"
popd >/dev/null

# reload gunicorn workers (graceful, no downtime, instant)
# NOTE: reload only cycles workers, not the master process. If changes to
# settings, wsgi.py, or packages don't take effect, do a full restart:
#   sudo systemctl restart tjai-gunicorn
sudo systemctl reload tjai-gunicorn
echo "[${SECONDS}s] gunicorn reloaded (quick reload — if changes don't take effect, run: sudo systemctl restart tjai-gunicorn)"
sudo systemctl restart tjai-tgbot
echo "[${SECONDS}s] tgbot restarted"
/var/www/tjai/.venv/bin/supervisorctl -c /var/www/tjai/deploy/supervisord.conf restart action-agent
echo "[${SECONDS}s] action-agent restarted"

echo "Deployment complete in ${SECONDS}s."
