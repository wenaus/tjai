from django.db import migrations, models


def renumber_duplicate_versions(apps, schema_editor):
    EntryVersion = apps.get_model('tjai_app', 'EntryVersion')
    duplicate_entry_ids = set(
        EntryVersion.objects.values('entry_id', 'version_num')
        .annotate(count=models.Count('id'))
        .filter(count__gt=1)
        .values_list('entry_id', flat=True)
    )

    for entry_id in duplicate_entry_ids:
        versions = EntryVersion.objects.filter(entry_id=entry_id).order_by(
            'timestamp', 'id'
        )
        for version_num, version in enumerate(versions.iterator(), start=1):
            if version.version_num != version_num:
                EntryVersion.objects.filter(pk=version.pk).update(
                    version_num=version_num
                )


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0014_entry_entries_search_gin'),
    ]

    operations = [
        migrations.RunPython(
            renumber_duplicate_versions,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='entryversion',
            constraint=models.UniqueConstraint(
                fields=('entry', 'version_num'),
                name='unique_entry_version_num',
            ),
        ),
    ]
