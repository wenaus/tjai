from django.db import models


class Context(models.Model):
    """Project or topic context for grouping entries."""
    name = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    timestamp_created = models.FloatField()
    timestamp_modified = models.FloatField()

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

    class Meta:
        db_table = 'entries'
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


class SysConfig(models.Model):
    """System-wide configuration parameters (server-authoritative)."""
    key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    description = models.TextField(null=True, blank=True)
    timestamp_modified = models.FloatField()

    class Meta:
        db_table = 'sysconfig'
