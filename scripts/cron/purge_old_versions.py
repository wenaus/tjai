#!/usr/bin/env python3
"""Purge entry versions older than 3 days, keeping the 2 most recent per entry."""
import sys
import time

sys.path.insert(0, '/home/admin/github/tjrepo/tjai/scripts')
import bootstrap  # noqa: E402, F401
from django.db import connection

MAX_AGE_DAYS = 3
KEEP_MIN = 2  # always keep the N most recent versions per entry


def purge():
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    cursor = connection.cursor()

    # Find versions older than cutoff that are NOT in the top KEEP_MIN per entry
    cursor.execute("""
        DELETE FROM entry_versions
        WHERE id IN (
            SELECT id FROM (
                SELECT id, timestamp,
                       ROW_NUMBER() OVER (PARTITION BY entry_id ORDER BY timestamp DESC) AS rn
                FROM entry_versions
            ) ranked
            WHERE rn > %s AND timestamp < %s
        )
    """, [KEEP_MIN, cutoff])

    deleted = cursor.rowcount
    print(f"Purged {deleted} old versions (cutoff: {MAX_AGE_DAYS}d, kept min {KEEP_MIN}/entry)")


if __name__ == '__main__':
    purge()
