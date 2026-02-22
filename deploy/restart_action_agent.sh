#!/bin/bash
# Restart the action agent via supervisord.
# Starts supervisord if not already running.
set -euo pipefail

CONF="/var/www/tjai/deploy/supervisord.conf"
SOCK="/tmp/tjai-supervisor.sock"
SCTL="/var/www/tjai/.venv/bin/supervisorctl -c $CONF"

if [ ! -S "$SOCK" ]; then
    echo "Starting supervisord..."
    /var/www/tjai/.venv/bin/supervisord -c "$CONF"
    sleep 1
fi

$SCTL restart action-agent
$SCTL status
