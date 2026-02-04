#!/bin/bash
# Restart tg_bot safely
# Usage: ./restart_tgbot.sh [--sync]
#   --sync: Copy dev files to production before restart

BOT_DIR="/var/www/tjai"
DEV_DIR="/home/admin/github/tjrepo/tjai"
LOG="/tmp/tg_bot.log"
PIDFILE="/tmp/tg_bot.pid"

# Sync dev to prod if requested
if [ "$1" = "--sync" ]; then
    echo "Syncing from dev..."
    rsync -a --delete "$DEV_DIR/tg_bot/" "$BOT_DIR/tg_bot/"
    rsync -a "$DEV_DIR/tj/" "$BOT_DIR/tj/"
fi

# Find existing bot process by checking stored PID first, then searching
oldpid=""
if [ -f "$PIDFILE" ]; then
    oldpid=$(cat "$PIDFILE")
    if ! ps -p "$oldpid" -o cmd= 2>/dev/null | grep -q "tg_bot"; then
        oldpid=""
    fi
fi

if [ -z "$oldpid" ]; then
    # Search for process - use specific pattern that won't match this script
    oldpid=$(ps aux | grep '[.]venv/bin/python -m tg_bot' | awk '{print $2}' | head -1)
fi

# Kill old process if found
if [ -n "$oldpid" ]; then
    echo "Stopping bot (PID $oldpid)..."
    kill "$oldpid" 2>/dev/null
    # Wait for clean shutdown
    for i in 1 2 3 4 5; do
        if ! ps -p "$oldpid" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    # Force kill if still running
    if ps -p "$oldpid" > /dev/null 2>&1; then
        kill -9 "$oldpid" 2>/dev/null
    fi
fi

# Start new instance
echo "Starting bot..."
cd "$BOT_DIR"
nohup .venv/bin/python -m tg_bot >> "$LOG" 2>&1 &
newpid=$!
echo "$newpid" > "$PIDFILE"

# Verify startup
sleep 2
if ps -p "$newpid" > /dev/null 2>&1; then
    echo "Bot running (PID $newpid)"
    tail -3 "$LOG"
else
    echo "ERROR: Bot failed to start"
    tail -20 "$LOG"
    exit 1
fi
