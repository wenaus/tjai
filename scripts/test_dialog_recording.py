#!/usr/bin/env python3
"""Check dialog timestamp and retry semantics in a rolled-back transaction."""
import bootstrap  # noqa: F401
import json
import uuid
from datetime import datetime

from django.db import transaction
from django.test import RequestFactory
from tjai_app.models import Entry, SysConfig, Tag
from tjai_app.views import api_dialog
from dialog_prep import fetch_dialog


def main():
    token = SysConfig.objects.get(key='gmail_addon_api_key').value
    factory = RequestFactory()
    payload = {
        'content': '[DIAG] Visible during-turn update', 'role': 'assistant',
        'client': 'claude-code', 'hostname': 'recorder-test',
        'session_id': str(uuid.uuid4()), 'source_id': 'message:thinking',
        'content_type': 'thinking', 'timestamp': '2026-09-11T17:58:31.208Z',
    }

    def post(body):
        response = api_dialog(factory.post('/tjai/api/dialog', data=json.dumps(body),
                             content_type='application/json',
                             HTTP_AUTHORIZATION=f'Bearer {token}'))
        return response.status_code, json.loads(response.content)

    with transaction.atomic():
        status, result = post(payload)
        assert status == 200, result
        entry = Entry.objects.get(id=result['entry_id'])
        assert entry.timestamp_created == datetime.fromisoformat(payload['timestamp']).timestamp()
        assert entry.data['content_type'] == 'thinking'
        assert entry.data['source_id'] == payload['source_id']
        status, repeated = post(payload)
        assert status == 200 and repeated == result, repeated
        assert Tag.objects.filter(entry=entry, tag_name='ccdialog').count() == 1
        assert Entry.objects.filter(data__session_id=payload['session_id']).count() == 1
        turns = fetch_dialog('2026-09-11')
        assert any(t['id'] == str(entry.id) and t['content'] == payload['content'] for t in turns)
        for invalid in ('bad', '2026-09-11T17:58:31', 123):
            assert post(dict(payload, timestamp=invalid))[0] == 400
        assert post(dict(payload, session_id=''))[0] == 400
        legacy = {k: v for k, v in payload.items() if k not in ('source_id', 'timestamp')}
        _, first = post(legacy)
        _, second = post(legacy)
        assert first['entry_id'] != second['entry_id']
        transaction.set_rollback(True)
    print('PASS: timestamps, retry identity, assessment input, validation, legacy writes; all rolled back')


if __name__ == '__main__':
    main()
