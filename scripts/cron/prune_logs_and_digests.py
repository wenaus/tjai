#!/usr/bin/env python3
"""Prune applog rows and health-digest files.

- INFO-level logs (level < 40): delete after 7 days
- ERROR-level logs (level >= 40): delete after 14 days
- Health-digest files: delete after 7 days
"""
import glob
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, '/home/admin/github/tjrepo/tjai/scripts')
import bootstrap  # noqa: E402, F401
from django.db import connection

INFO_MAX_DAYS = 7
ERROR_MAX_DAYS = 14
DIGEST_MAX_DAYS = 7
DIGEST_DIR = '/var/www/tjai/data/health-digest'


def prune_applog():
    now = time.time()
    cursor = connection.cursor()

    # INFO and below (level < 40): 7 days
    info_cutoff = datetime.utcfromtimestamp(now - INFO_MAX_DAYS * 86400)
    cursor.execute("DELETE FROM applog WHERE level < 40 AND timestamp < %s", [info_cutoff])
    info_deleted = cursor.rowcount

    # ERROR and above (level >= 40): 14 days
    error_cutoff = datetime.utcfromtimestamp(now - ERROR_MAX_DAYS * 86400)
    cursor.execute("DELETE FROM applog WHERE level >= 40 AND timestamp < %s", [error_cutoff])
    error_deleted = cursor.rowcount

    print(f"Applog: pruned {info_deleted} info rows (>{INFO_MAX_DAYS}d), "
          f"{error_deleted} error rows (>{ERROR_MAX_DAYS}d)")


def prune_health_digests():
    if not os.path.isdir(DIGEST_DIR):
        print(f"Health-digest dir not found: {DIGEST_DIR}")
        return

    cutoff = datetime.now() - timedelta(days=DIGEST_MAX_DAYS)
    deleted = 0
    for path in glob.glob(os.path.join(DIGEST_DIR, '*')):
        fname = os.path.basename(path)
        # Files are named YYYY-MM-DD.json or YYYY-MM-DD.md
        date_part = fname.split('.')[0]
        try:
            file_date = datetime.strptime(date_part, '%Y-%m-%d')
        except ValueError:
            continue
        if file_date < cutoff:
            os.remove(path)
            deleted += 1

    print(f"Health-digest: deleted {deleted} files (>{DIGEST_MAX_DAYS}d)")


if __name__ == '__main__':
    prune_applog()
    prune_health_digests()
