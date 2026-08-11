"""Create the wrangle-ai worker table (docs/wrangler.md).

DDL matches wrangle_ai.postgres.SCHEMA for wrangle_workers, inlined here so the
migration is a frozen snapshot (the Django norm) and migrate does not import the
optional dependency. Column defaults live in the database because PgBullpen
inserts outside the ORM. The WrangleWorker model is unmanaged; state_operations
teaches the migration state about it without further schema management.
"""
from django.db import migrations, models


DDL = [
    """CREATE TABLE IF NOT EXISTS wrangle_workers (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        payload     JSONB NOT NULL DEFAULT '{}',
        status      TEXT NOT NULL DEFAULT 'pending',
        result      JSONB,
        error       TEXT,
        attempts    INTEGER NOT NULL DEFAULT 0,
        claimed_by  TEXT,
        claimed_pid INTEGER,
        doer_pid    INTEGER,
        claimed_at  TIMESTAMPTZ,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS wrangle_workers_pending
       ON wrangle_workers(type, created_at) WHERE status = 'pending'""",
    """CREATE INDEX IF NOT EXISTS wrangle_workers_running
       ON wrangle_workers(claimed_by) WHERE status = 'running'""",
]


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0022_rename_swf_dispatcher_capcom_state'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(stmt, reverse_sql=migrations.RunSQL.noop)
                for stmt in DDL
            ],
            state_operations=[
                migrations.CreateModel(
                    name='WrangleWorker',
                    fields=[
                        ('id', models.TextField(primary_key=True, serialize=False)),
                        ('type', models.TextField()),
                        ('payload', models.JSONField(default=dict)),
                        ('status', models.TextField(default='pending')),
                        ('result', models.JSONField(blank=True, null=True)),
                        ('error', models.TextField(blank=True, null=True)),
                        ('attempts', models.IntegerField(default=0)),
                        ('claimed_by', models.TextField(blank=True, null=True)),
                        ('claimed_pid', models.IntegerField(blank=True, null=True)),
                        ('doer_pid', models.IntegerField(blank=True, null=True)),
                        ('claimed_at', models.DateTimeField(blank=True, null=True)),
                        ('created_at', models.DateTimeField()),
                        ('finished_at', models.DateTimeField(blank=True, null=True)),
                    ],
                    options={'db_table': 'wrangle_workers', 'managed': False},
                ),
            ],
        ),
    ]
