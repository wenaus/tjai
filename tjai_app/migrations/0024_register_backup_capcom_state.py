import json
import time

from django.db import migrations


def register_backup_state_source(apps, schema_editor):
    SysConfig = apps.get_model('tjai_app', 'SysConfig')
    row = (SysConfig.objects.using(schema_editor.connection.alias)
           .filter(key='capcom_sources').first())
    if not row or not row.value:
        return
    try:
        sources = json.loads(row.value)
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(sources, list):
        return
    if any(isinstance(s, dict) and s.get('source') == 'server-backup'
           for s in sources):
        return

    sources.append({
        'source': 'server-backup',
        'kind': 'state',
        'mode': 'poll',
        'cadence': 60,
        'enabled': True,
        'last_run': 0,
        'note': 'Backup health: recency, completeness, size vs trailing week',
    })
    row.value = json.dumps(sources)
    row.timestamp_modified = time.time()
    row.save(update_fields=['value', 'timestamp_modified'])


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0023_wrangle_workers'),
    ]

    operations = [
        migrations.RunPython(
            register_backup_state_source,
            migrations.RunPython.noop,
        ),
    ]
