"""Rebuild the tag_stats table from tags + entries."""

from django.db import connection


def rebuild_tag_stats():
    """Truncate and repopulate tag_stats from current tags and entries."""
    with connection.cursor() as cursor:
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
            WHERE e.deleted_at IS NULL
            GROUP BY t.tag_name
        """)
