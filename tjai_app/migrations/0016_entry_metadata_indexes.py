from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.fields.json import KeyTransform


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('tjai_app', '0015_entry_version_number_integrity'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                KeyTransform('entry_id', 'data'),
                name='idx_entries_data_entryid',
            ),
        ),
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                KeyTransform('event_date', 'data'),
                name='idx_entries_journal_event',
                condition=models.Q(
                    kind='journal',
                    deleted_at__isnull=True,
                    mmdd__isnull=True,
                ),
            ),
        ),
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                KeyTransform('hostname', 'data'),
                models.F('timestamp_created'),
                name='idx_entries_dialog_host_ts',
                condition=models.Q(deleted_at__isnull=True),
            ),
        ),
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                KeyTransform('worker_target', 'data'),
                models.F('timestamp_modified'),
                name='idx_entries_worker_target',
                condition=models.Q(
                    status='active',
                    deleted_at__isnull=True,
                ),
            ),
        ),
    ]
