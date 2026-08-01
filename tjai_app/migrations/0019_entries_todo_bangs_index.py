from django.db import migrations

# Partial index whose predicate is the todo-bang line regex (leading 3+
# bangs, modulo a markdown list prefix). It holds only the handful of
# rows carrying bang lines, so the todo-bangs extraction is an index hit
# (~0.1ms) instead of a full content scan (~0.7s). The pattern must stay
# identical to TODO_BANG_SQL_RE in tjai_app/views.py — the planner proves
# the query's regex implies the index predicate only on exact match.
# A trigram index cannot serve here: pg_trgm ignores punctuation.

BANG_PAT = r'(^|\n)[ \t]*([-*+][ \t]+|[0-9]+[.)][ \t]+)?!{3,}'


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0018_entry_worker_claimed_index'),
    ]

    operations = [
        migrations.RunSQL(
            sql=("CREATE INDEX IF NOT EXISTS entries_todo_bangs "
                 "ON entries (timestamp_modified) "
                 f"WHERE content ~ '{BANG_PAT}'"),
            reverse_sql="DROP INDEX IF EXISTS entries_todo_bangs",
        ),
    ]
