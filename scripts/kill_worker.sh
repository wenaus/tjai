#!/bin/sh
# Deterministic kill for the Mac-side tj_agent remote worker.
#
# Two-step kill that makes server state reflect reality immediately:
#
#   1. POST status='failed' for the worker's in-flight claim (if any)
#      via `python -m tj_agent abort`. The server transitions the
#      sub-entry from 'active' to 'failed' and the UI stops showing
#      it as running. Without this step, the claim sits 'active'
#      until WORKER_CLAIM_STALE_SECONDS (2h) auto-reclaims — that
#      much lag between reality and UI is what this script exists
#      to eliminate.
#
#   2. `launchctl bootout` — actually stop the tj_agent process.
#
# If tj_agent is already dead, step 1 still works (it only reads the
# local ~/.tjai/current_claim.json marker and hits the server API);
# step 2 is a harmless no-op. Safe to run unconditionally.
#
# This is the script to use for operator kill. Do not invoke ad-hoc
# `launchctl bootout` without it — that loses step 1 and leaves the
# server showing the sub-entry as running for up to 2h.
#
# Reason text (optional): concatenated and passed to the abort POST
# as the `error` field on the /api/worker/result payload. Shows up in
# the server's applog and is visible in the sub-entry's content.
#
#   ./scripts/kill_worker.sh
#   ./scripts/kill_worker.sh prompt was stale, restaging gemma
#
# Location of this script: the tjai repo. See docs/remote-workers.md
# for context and design.

set -e

PLIST="$HOME/Library/LaunchAgents/com.tj_agent.plist"
VENV_PY="$HOME/.tjai/venv/bin/python3"
REPO="$HOME/github/tjrepo/tjai"

REASON="$*"
if [ -z "$REASON" ]; then
  REASON="operator kill via scripts/kill_worker.sh"
fi

echo "==> Aborting in-flight claim on server (if any)..."
if [ -x "$VENV_PY" ] && [ -d "$REPO" ]; then
  if [ -f "$HOME/.tjai/env" ]; then
    . "$HOME/.tjai/env"
  fi
  PYTHONPATH="$REPO" "$VENV_PY" -m tj_agent abort "$REASON" || {
    echo "WARN: abort step failed; proceeding to bootout anyway." >&2
  }
else
  echo "WARN: venv python or tjai repo not found at expected paths; " >&2
  echo "      skipping abort step. Server will take up to 2h to reclaim." >&2
  echo "      Expected: $VENV_PY and $REPO" >&2
fi

echo "==> Stopping tj_agent (launchctl bootout)..."
if [ -f "$PLIST" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" || {
    echo "NOTE: bootout returned non-zero; agent may already be down." >&2
  }
else
  echo "WARN: plist not found at $PLIST; nothing to bootout." >&2
fi

echo "==> Done. tj_agent should be stopped; claim (if any) posted as failed."
echo "    To restart: launchctl bootstrap gui/\$(id -u) $PLIST"
