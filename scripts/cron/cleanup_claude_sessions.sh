#!/bin/bash
# Purge Claude Code session detritus older than 2 days.
# tool-results, debug logs, file-history — accumulated by every claude -p run.
# Schedule: daily at 04:00 via crontab

set -euo pipefail

DAYS=2

find ~/.claude/projects -name "tool-results" -type d -mtime +$DAYS -exec rm -rf {} + 2>/dev/null || true
find ~/.claude/debug -type f -mtime +$DAYS -delete 2>/dev/null || true
find ~/.claude/file-history -type f -mtime +$DAYS -delete 2>/dev/null || true

# Remove empty session dirs left behind
find ~/.claude/projects -mindepth 2 -maxdepth 2 -type d -empty -delete 2>/dev/null || true
