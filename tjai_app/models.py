import logging

from django.contrib.postgres.search import SearchVectorField
from django.db import connection, models, transaction
from django.db.models import Max
from django.db.models.fields.json import KeyTransform


class Context(models.Model):
    """Project or topic context for grouping entries."""
    name = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    timestamp_created = models.FloatField()
    timestamp_modified = models.FloatField()
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'contexts'


class Entry(models.Model):
    """Main entries table - memories, todos, bookmarks, journal, profile, ai guidelines."""
    id = models.CharField(max_length=36, primary_key=True)  # UUID
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='children', db_column='parent_id'
    )
    content = models.TextField()
    kind = models.CharField(max_length=50)  # memory, bookmark, todo, journal, profile, ai, list
    timestamp_created = models.FloatField()
    timestamp_modified = models.FloatField()
    context = models.ForeignKey(
        Context, on_delete=models.SET_NULL, null=True, blank=True,
        db_column='context', to_field='name'
    )
    is_dirty = models.IntegerField(default=1)
    deleted_at = models.FloatField(null=True, blank=True)  # Soft delete
    name = models.CharField(max_length=255, null=True, blank=True)  # Optional unique name
    priority = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    data = models.JSONField(null=True, blank=True)  # Extensible metadata
    mmdd = models.IntegerField(null=True, blank=True, db_index=True)  # Annual event month-day (e.g. 315 = March 15)
    search_vector = SearchVectorField(null=True)  # Full-text search index

    class Meta:
        db_table = 'entries'
        indexes = [
            models.Index(fields=['search_vector'], name='entries_search_gin'),
            models.Index(
                KeyTransform('entry_id', 'data'),
                name='idx_entries_data_entryid',
            ),
            models.Index(
                KeyTransform('event_date', 'data'),
                name='idx_entries_journal_event',
                condition=models.Q(
                    kind='journal',
                    deleted_at__isnull=True,
                    mmdd__isnull=True,
                ),
            ),
            models.Index(
                KeyTransform('hostname', 'data'),
                models.F('timestamp_created'),
                name='idx_entries_dialog_host_ts',
                condition=models.Q(deleted_at__isnull=True),
            ),
            models.Index(
                KeyTransform('worker_target', 'data'),
                models.F('timestamp_modified'),
                name='idx_entries_worker_target',
                condition=models.Q(
                    status='active',
                    deleted_at__isnull=True,
                ),
            ),
            models.Index(
                fields=['kind', 'status', 'context'],
                name='idx_entries_dash_facets',
                condition=models.Q(deleted_at__isnull=True),
            ),
            models.Index(
                KeyTransform('worker_claimed_by', 'data'),
                name='idx_entries_worker_claimed',
                condition=models.Q(deleted_at__isnull=True),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['context', 'name'],
                name='unique_context_name',
                condition=models.Q(name__isnull=False)
            )
        ]


class Tag(models.Model):
    """Tags for entries (many-to-many)."""
    tag_name = models.CharField(max_length=255)
    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE,
        related_name='tags', db_column='entry_id'
    )

    class Meta:
        db_table = 'tags'
        constraints = [
            models.UniqueConstraint(fields=['tag_name', 'entry'], name='unique_tag_entry')
        ]
        indexes = [
            models.Index(fields=['tag_name'], name='idx_tags_tag_name')
        ]


class SubNote(models.Model):
    """Sub-notes attached to entries."""
    id = models.CharField(max_length=36, primary_key=True)  # UUID
    parent = models.ForeignKey(
        Entry, on_delete=models.CASCADE,
        related_name='sub_notes', db_column='parent_id'
    )
    content = models.TextField()
    timestamp_created = models.FloatField()
    data = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'sub_notes'


class KozyChat(models.Model):
    """KozyKorner chat messages — persistent family chat alongside video."""
    id = models.AutoField(primary_key=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    sender = models.CharField(max_length=50)  # Torre, Mom, Jarvis, etc.
    content = models.TextField()

    class Meta:
        db_table = 'kozy_chat'
        ordering = ['timestamp']


class Relation(models.Model):
    """Relations between entries — the edges of the goal/knowledge graph."""
    id = models.CharField(max_length=36, primary_key=True)  # UUID
    entry1 = models.ForeignKey(
        Entry, on_delete=models.CASCADE,
        related_name='relations_as_entry1', db_column='entry1_id'
    )
    entry2 = models.ForeignKey(
        Entry, on_delete=models.CASCADE,
        related_name='relations_as_entry2', db_column='entry2_id'
    )
    relation_type = models.CharField(max_length=255)
    data = models.JSONField(null=True, blank=True)
    timestamp_created = models.FloatField()
    timestamp_modified = models.FloatField()

    class Meta:
        db_table = 'relations'
        constraints = [
            models.UniqueConstraint(
                fields=['entry1', 'entry2'],
                name='unique_relation_pair'
            ),
            models.CheckConstraint(
                condition=~models.Q(entry1=models.F('entry2')),
                name='no_self_relation'
            ),
        ]
        indexes = [
            models.Index(fields=['entry1'], name='idx_relations_entry1'),
            models.Index(fields=['entry2'], name='idx_relations_entry2'),
        ]


class SyncMetadata(models.Model):
    """Sync state tracking."""
    key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    timestamp_updated = models.FloatField()

    class Meta:
        db_table = 'sync_metadata'


class Machine(models.Model):
    """Track client machines for sync."""
    machine_id = models.CharField(max_length=255, primary_key=True)
    hostname = models.CharField(max_length=255, null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    last_sync = models.FloatField(null=True, blank=True)
    timestamp_created = models.FloatField()
    is_active = models.IntegerField(default=1)

    class Meta:
        db_table = 'machines'


class TagStats(models.Model):
    """Precomputed per-tag metadata for efficient dashboard queries."""
    tag_name = models.CharField(max_length=255, primary_key=True)
    entry_count = models.IntegerField(default=0)
    is_context_only = models.BooleanField(default=False)
    only_context = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.FloatField()

    class Meta:
        db_table = 'tag_stats'


class AppLog(models.Model):
    """Application log entries, stored in DB for dashboard visibility."""
    LEVEL_CHOICES = [
        (logging.CRITICAL, 'CRITICAL'),
        (logging.ERROR, 'ERROR'),
        (logging.WARNING, 'WARNING'),
        (logging.INFO, 'INFO'),
        (logging.DEBUG, 'DEBUG'),
    ]
    source = models.CharField(max_length=100, db_index=True)
    timestamp = models.DateTimeField(db_index=True)
    level = models.IntegerField(choices=LEVEL_CHOICES, default=logging.INFO, db_index=True)
    levelname = models.CharField(max_length=50)
    message = models.TextField()
    extra_data = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'applog'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'source']),
        ]


class RssItem(models.Model):
    """RSS feed items — ephemeral until user acts on them."""
    guid = models.TextField(primary_key=True)  # feed guid or url hash
    feed_url = models.TextField()
    source = models.TextField()       # feed title
    category = models.TextField(default='uncategorized')
    title = models.TextField()
    url = models.TextField()          # article link
    precis = models.TextField(default='')
    published = models.DateTimeField(null=True)
    fetched = models.DateTimeField()
    read = models.BooleanField(default=False, db_index=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'rss_items'
        indexes = [
            models.Index(fields=['read', 'category', 'source']),
        ]


class EntryVersion(models.Model):
    """Revision history for entries — snapshots previous state before each save."""
    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE,
        related_name='versions', db_column='entry_id'
    )
    version_num = models.IntegerField(default=0)  # immutable: v1, v2, v3...
    content = models.TextField()
    data = models.JSONField(null=True, blank=True)
    changed_by = models.CharField(max_length=100, default='unknown')
    timestamp = models.FloatField()  # when the snapshot was taken

    class Meta:
        db_table = 'entry_versions'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['entry', '-timestamp'], name='idx_entryver_entry_ts'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['entry', 'version_num'],
                name='unique_entry_version_num',
            ),
        ]


def create_entry_version(entry, changed_by='unknown'):
    """Create the next version while serializing allocation per entry."""
    import time as _time

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [str(entry.pk)],
            )
        max_num = (
            EntryVersion.objects.filter(entry_id=entry.pk)
            .aggregate(Max('version_num'))['version_num__max']
            or 0
        )
        return EntryVersion.objects.create(
            entry_id=entry.pk,
            version_num=max_num + 1,
            content=entry.content,
            data=entry.data,
            changed_by=changed_by,
            timestamp=_time.time(),
        )


def snapshot_entry(entry, changed_by='unknown'):
    """Snapshot an entry's current state into the version history.
    Call before any operation that modifies or deletes content."""
    return create_entry_version(entry, changed_by)


class SysConfig(models.Model):
    """System-wide configuration parameters (server-authoritative)."""
    key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    description = models.TextField(null=True, blank=True)
    timestamp_modified = models.FloatField()

    class Meta:
        db_table = 'sysconfig'
