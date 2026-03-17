#!/bin/bash
# Restart tg_bot via systemd
# Usage: ./restart_tgbot.sh [--sync]
#   --sync: Copy dev files to production before restart

BOT_DIR="/var/www/tjai"
DEV_DIR="/home/admin/github/tjrepo/tjai"

# Sync dev to prod if requested
if [ "$1" = "--sync" ]; then
    echo "Syncing from dev..."
    rsync -a --delete "$DEV_DIR/tg_bot/" "$BOT_DIR/tg_bot/"
    rsync -a "$DEV_DIR/tj/" "$BOT_DIR/tj/"
fi

sudo systemctl restart tjai-tgbot
sleep 2
sudo systemctl status tjai-tgbot --no-pager | head -15
