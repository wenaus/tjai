from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.fields.json import KeyTransform


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('tjai_app', '0017_entry_dashboard_facets_index'),
    ]

    operations = [
        # Serves worker_poll's free-capacity reset, which runs
        # data__worker_claimed_by=machine_id + deleted_at__isnull=True on
        # every remote-worker poll (~50s cadence) and otherwise seq-scans
        # the full entries table each time.
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                KeyTransform('worker_claimed_by', 'data'),
                name='idx_entries_worker_claimed',
                condition=models.Q(deleted_at__isnull=True),
            ),
        ),
    ]
