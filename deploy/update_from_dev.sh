#!/usr/bin/env bash
# Sync repo to /var/www/tjai, install deps, migrate, restart services.
# Interpreter + venv are built by deploy/make_venv.sh from .python-version
# (uv-managed CPython) -- the single source of truth shared with dev setup.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET_DIR=/var/www/tjai
VENV=$TARGET_DIR/.venv
SECONDS=0

# rsync code (preserve .venv, .env, data/)
# --chmod keeps dirs/files world-readable for www-data (gunicorn) AND sets the
# execute bit on files, so scripts (e.g. deploy/make_venv.sh) survive rsync runnable.
rsync -a --chmod=D755,F755 \
  --exclude '.venv' --exclude '.venv.*' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'data/' \
  "$REPO_ROOT/" "$TARGET_DIR/"
echo "[${SECONDS}s] rsync done"

# ensure env
if [[ ! -f $TARGET_DIR/.env ]]; then
  echo "WARNING: No .env file found. Copy from .env.example and configure."
  exit 1
fi

# Build/refresh the venv via the single source of truth (deploy/make_venv.sh).
# Runs when the venv is missing or any requirements file / .python-version
# changed. make_venv.sh self-heals the interpreter: if .python-version no longer
# matches the venv's Python, it rebuilds -- the pin can never silently drift.
REQ_HASH=$(cat "$TARGET_DIR"/requirements/*.txt "$TARGET_DIR/.python-version" | md5sum | cut -d' ' -f1)
REQ_HASH_FILE="$TARGET_DIR/.last_requirements_hash"
if [[ ! -d $VENV ]] || [[ ! -f "$REQ_HASH_FILE" ]] || [[ "$(cat "$REQ_HASH_FILE")" != "$REQ_HASH" ]]; then
  "$TARGET_DIR/deploy/make_venv.sh" "$VENV" prod
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "Requirements & interpreter unchanged, skipping venv sync."
fi

# one-time cleanup of the retired package
if "$VENV/bin/python" -c "import importlib.metadata as m; m.version('django-mcp-server')" >/dev/null 2>&1; then
  uv pip uninstall --python "$VENV/bin/python" django-mcp-server
fi
echo "[${SECONDS}s] deps done"

# migrate
pushd "$TARGET_DIR" >/dev/null
"$VENV/bin/python" manage.py migrate --noinput
echo "[${SECONDS}s] migrate done"

# Every rendered CAPCOM state must have one complete source row. This catches
# incomplete additions before any service is reloaded onto the new code.
"$VENV/bin/python" manage.py validate_capcom_sources
echo "[${SECONDS}s] capcom sources validated"

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
if systemctl cat tjai-mcp-asgi >/dev/null 2>&1; then
  sudo systemctl restart tjai-mcp-asgi
  echo "[${SECONDS}s] mcp asgi restarted"
else
  echo "[${SECONDS}s] tjai-mcp-asgi service not installed, skipping mcp restart"
fi
sudo systemctl restart tjai-tgbot
echo "[${SECONDS}s] tgbot restarted"
# Schedule a graceful action-agent restart rather than hard-restarting:
# a hard restart kills in-progress actions. The agent polls the flag,
# finishes any current action, exits, and supervisord restarts it on the
# new code (within seconds when idle).
# Set, then verify with an independent psql read-back: the set has
# exited 0 without landing, so success is judged by the observed flag
# state, not the exit code.
if ! (cd "$TARGET_DIR" && sudo -u www-data ./.venv/bin/python manage.py shell -c "
import time
from tjai_app.models import SysConfig
SysConfig.objects.update_or_create(key='action_agent_restart_requested', defaults={'value': '1', 'timestamp_modified': time.time()})
" >/dev/null); then
  echo "[${SECONDS}s] WARNING: restart-flag set command failed (see errors above)"
fi
RESTART_FLAG=$( (set -a; source "$TARGET_DIR/.env"; set +a
  psql "$DJANGO_DATABASE_URL" -tAc \
    "SELECT value FROM sysconfig WHERE key='action_agent_restart_requested'") 2>/dev/null \
  || echo READBACK_FAILED)
if [[ "$RESTART_FLAG" == "1" ]]; then
  echo "[${SECONDS}s] action-agent graceful restart scheduled (restarts after any in-progress action)"
else
  echo "[${SECONDS}s] WARNING: action-agent restart flag not verified (read back: '${RESTART_FLAG}')."
  echo "  The agent may still be running OLD code. Either the agent consumed the"
  echo "  flag already (agent log shows 'Restart requested — exiting' just now)"
  echo "  or the set never landed. Set it manually with:"
  echo "  cd $TARGET_DIR && sudo -u www-data ./.venv/bin/python manage.py shell -c \\"
  echo "    \"import time; from tjai_app.models import SysConfig; SysConfig.objects.update_or_create(key='action_agent_restart_requested', defaults={'value': '1', 'timestamp_modified': time.time()})\""
fi

echo "Deployment complete in ${SECONDS}s."
