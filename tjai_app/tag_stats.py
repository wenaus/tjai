"""Rebuild the tag_stats table from tags + entries."""

import time

from django.db import connection, transaction


# Stable namespace for the advisory lock that serializes concurrent
# rebuild_tag_stats() calls. The value is arbitrary; only uniqueness
# within the database matters.
_TAG_STATS_LOCK_ID = 0x7461675F73746174  # 'tag_stat' as ascii

# Skip rebuild if last one ran within this many seconds — tag stats are
# slow-changing and bursty rebuilds are expensive under the advisory lock.
_MIN_REBUILD_INTERVAL_SECONDS = 60


def rebuild_tag_stats(force=False):
    """Truncate and repopulate tag_stats from current tags and entries.

    Skipped if the last rebuild was recent (within
    _MIN_REBUILD_INTERVAL_SECONDS). Set `force=True` to bypass the recency
    gate. Serialized across concurrent callers via a Postgres advisory
    lock — without it, two parallel rebuilds can race their DELETE+INSERT
    and violate tag_stats_pkey.
    """
    if not force:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(updated_at) FROM tag_stats")
            row = cursor.fetchone()
        last = float(row[0]) if row and row[0] is not None else 0.0
        if (time.time() - last) < _MIN_REBUILD_INTERVAL_SECONDS:
            return

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_TAG_STATS_LOCK_ID])
            # Re-check inside the lock so the rebuild only fires once per
            # interval even if multiple requests passed the outer check.
            if not force:
                cursor.execute("SELECT MAX(updated_at) FROM tag_stats")
                row = cursor.fetchone()
                last = float(row[0]) if row and row[0] is not None else 0.0
                if (time.time() - last) < _MIN_REBUILD_INTERVAL_SECONDS:
                    return
            cursor.execute("DELETE FROM tag_stats")
            cursor.execute("""
                INSERT INTO tag_stats (tag_name, entry_count, is_context_only, only_context, updated_at)
                SELECT t.tag_name, COUNT(*),
                    CASE WHEN COUNT(DISTINCT COALESCE(e.context, '')) = 1
                         AND MAX(e.context) IS NOT NULL THEN TRUE ELSE FALSE END,
                    CASE WHEN COUNT(DISTINCT COALESCE(e.context, '')) = 1
                         AND MAX(e.context) IS NOT NULL THEN MAX(e.context) ELSE NULL END,
                    EXTRACT(EPOCH FROM NOW())
                FROM tags t JOIN entries e ON t.entry_id = e.id
                WHERE e.deleted_at IS NULL AND COALESCE(e.status, '') != 'archive'
                GROUP BY t.tag_name
            """)
