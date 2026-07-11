from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('tjai_app', '0016_entry_metadata_indexes'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='entry',
            index=models.Index(
                fields=['kind', 'status', 'context'],
                name='idx_entries_dash_facets',
                condition=models.Q(deleted_at__isnull=True),
            ),
        ),
    ]
