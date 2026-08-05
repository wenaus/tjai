import json
import time

from django.db import migrations


def _rename_source(apps, schema_editor, old_name, new_name):
    SysConfig = apps.get_model('tjai_app', 'SysConfig')
    manager = SysConfig.objects.using(schema_editor.connection.alias)

    state_row = manager.filter(key='capcom_state').first()
    if state_row and state_row.value:
        try:
            state = json.loads(state_row.value)
        except (TypeError, json.JSONDecodeError):
            state = None
        if isinstance(state, dict) and old_name in state:
            if new_name not in state:
                state[new_name] = state[old_name]
            del state[old_name]
            state_row.value = json.dumps(state)
            state_row.timestamp_modified = time.time()
            state_row.save(update_fields=['value', 'timestamp_modified'])

    sources_row = manager.filter(key='capcom_sources').first()
    if sources_row and sources_row.value:
        try:
            sources = json.loads(sources_row.value)
        except (TypeError, json.JSONDecodeError):
            sources = None
        if isinstance(sources, list):
            has_new = any(
                isinstance(source, dict) and source.get('source') == new_name
                for source in sources
            )
            renamed = []
            changed = False
            for source in sources:
                if not isinstance(source, dict) or source.get('source') != old_name:
                    renamed.append(source)
                elif not has_new:
                    source = dict(source)
                    source['source'] = new_name
                    source['note'] = (
                        'Mattermost bot activity from swf-monitor'
                        if new_name == 'swf-bot'
                        else 'Dispatcher activity from swf-monitor'
                    )
                    renamed.append(source)
                    has_new = True
                    changed = True
                else:
                    changed = True
            if changed:
                sources_row.value = json.dumps(renamed)
                sources_row.timestamp_modified = time.time()
                sources_row.save(update_fields=['value', 'timestamp_modified'])

    order_row = manager.filter(key='capcom_state_order').first()
    if order_row and order_row.value:
        try:
            order = json.loads(order_row.value)
        except (TypeError, json.JSONDecodeError):
            order = None
        if isinstance(order, list) and old_name in order:
            renamed = []
            for source in order:
                source = new_name if source == old_name else source
                if source not in renamed:
                    renamed.append(source)
            order_row.value = json.dumps(renamed)
            order_row.timestamp_modified = time.time()
            order_row.save(update_fields=['value', 'timestamp_modified'])


def rename_swf_dispatcher(apps, schema_editor):
    _rename_source(apps, schema_editor, 'swf-dispatcher', 'swf-bot')


def restore_swf_dispatcher(apps, schema_editor):
    _rename_source(apps, schema_editor, 'swf-bot', 'swf-dispatcher')


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0021_register_swf_capcom_states'),
    ]

    operations = [
        migrations.RunPython(rename_swf_dispatcher, restore_swf_dispatcher),
    ]
