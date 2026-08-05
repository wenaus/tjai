import json
import time

from django.db import migrations


SWF_STATE_SOURCES = (
    ('swf-alarms', 'Active SWF alarms from swf-monitor'),
    ('swf-dispatcher', 'Dispatcher activity from swf-monitor'),
    ('swf-user', 'Configured-user testbed and PanDA state from swf-monitor'),
)


def register_swf_state_sources(apps, schema_editor):
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

    existing = {
        source.get('source') for source in sources if isinstance(source, dict)
    }
    last_run = max((
        source.get('last_run') or 0
        for source in sources
        if isinstance(source, dict)
        and (source.get('collector') or source.get('source')) == 'swf-monitor'
    ), default=0)
    for source, note in SWF_STATE_SOURCES:
        if source in existing:
            continue
        sources.append({
            'source': source,
            'kind': 'state',
            'mode': 'poll',
            'collector': 'swf-monitor',
            'cadence': 10,
            'enabled': True,
            'last_run': last_run,
            'note': note,
        })

    row.value = json.dumps(sources)
    row.timestamp_modified = time.time()
    row.save(update_fields=['value', 'timestamp_modified'])


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0020_notice'),
    ]

    operations = [
        migrations.RunPython(
            register_swf_state_sources,
            migrations.RunPython.noop,
        ),
    ]
