#!/usr/bin/env python3
"""Check the dialog source/identity boundary in one rolled-back transaction."""

import json
import uuid

import bootstrap  # noqa: F401

from django.db import transaction
from django.test import RequestFactory

from tjai_app.dialog_context import CURRENT_DIALOG_CONTEXT, DIALOG_TAG
from tjai_app.dialog_people import (SYNC_KEY, apply_people_filters, daily_counts,
                                  dialog_options, dialog_population)
from tjai_app.models import Entry, SysConfig, Tag
from tjai_app.teamcomms_dialog import (message_entry_id, person, project_message,
                                     refresh_exchange_people)
from tjai_app.views import dashboard_search, dashboard_status


def identity(name):
    return {'participant_id': str(uuid.uuid4()), 'participant_name': name + ' AI',
            'participant_kind': 'ai', 'operator_id': str(uuid.uuid4()), 'operator_name': name}


def main():
    owner, collaborator, other = [identity(name) for name in ('Torre', 'Collaborator', 'Other')]
    source = 'https://dialog-boundary.invalid'
    marker = 'dialogboundary' + uuid.uuid4().hex
    factory = RequestFactory()

    def message(author, text, parent=None):
        return {'message_id': str(uuid.uuid4()), 'author_id': author['participant_id'],
                'created_at': '2026-10-27T21:00:00+00:00', 'reply_to': parent,
                'content': marker + ' ' + text, 'topic': '',
                'publication_snapshot': {'name': author['participant_name'], 'kind': 'ai'},
                'audience': {}, 'viewer_relation': {'sent': True, 'received': False}}

    with transaction.atomic():
        SysConfig.objects.update_or_create(key=SYNC_KEY, defaults={
            'value': json.dumps({'local_person_id': owner['operator_id']}),
            'timestamp_modified': 0,
        })
        incoming = message(collaborator, 'question')
        outgoing = message(owner, 'reply', incoming['message_id'])
        followup = message(other, 'followup', outgoing['message_id'])
        unrelated = message(other, 'unrelated')
        events = [(incoming, collaborator, [owner, owner]), (outgoing, owner, [collaborator]),
                  (followup, other, [owner]), (unrelated, other, [owner])]
        for row, author, recipients in events:
            assert project_message(source, row, author, recipients)
        assert not project_message(source, incoming, collaborator, [owner])
        refresh_exchange_people()
        projection_ids = [message_entry_id(source, row['message_id']) for row, _, _ in events]

        def native(role, **extra):
            row = Entry.objects.create(id=str(uuid.uuid4()), kind='memory',
                context_id=CURRENT_DIALOG_CONTEXT, content=marker + ' native',
                timestamp_created=1793134800, timestamp_modified=1793134800,
                data={'role': role, 'client': 'codex', 'session_id': 'same-session', **extra})
            Tag.objects.create(entry=row, tag_name=DIALOG_TAG)
            return row

        human, assistant = native('user'), native('assistant')
        echo = native('peer', peer_source='teamcomms', message_id=incoming['message_id'])
        unknown = native('peer', peer_source='teamcomms', message_id=str(uuid.uuid4()))
        ids = projection_ids + [human.id, assistant.id, echo.id, unknown.id]
        visible = dialog_population(Entry.objects.filter(id__in=ids))
        assert set(visible.values_list('id', flat=True)) == set(ids) - {echo.id}
        selected = apply_people_filters(visible, {'people': collaborator['operator_id']})
        assert set(selected.values_list('id', flat=True)) == set(projection_ids[:3])
        only_tc = apply_people_filters(visible, {'teamcomms': '1'})
        assert set(only_tc.values_list('id', flat=True)) == set(projection_ids + [unknown.id])
        local = apply_people_filters(visible, {'people': owner['operator_id']})
        assert human.id in set(local.values_list('id', flat=True))
        assert assistant.id in set(local.values_list('id', flat=True))
        assert unknown.id not in set(local.values_list('id', flat=True))
        assert echo.data['role'] == Entry.objects.get(pk=echo.id).data['role'] == 'peer'
        assert dict((p['id'], p['name']) for p in dialog_options())[collaborator['operator_id']] == 'Collaborator'
        assert person({**other, 'operator_id': None, 'operator_name': None})[0] == other['participant_id']
        assert sum(row['count'] for row in daily_counts({'people': collaborator['operator_id']})) == 3

        request = factory.get('/tjai/api/dashboard/status', {
            'view': 'dialog', 'people': collaborator['operator_id'], 'teamcomms': '1',
        })
        response = dashboard_status.__wrapped__(request)
        assert response.status_code == 200, response.content
        result = json.loads(response.content)
        assert {row['id'] for row in result['recent_entries']} == set(projection_ids[:3])
        assert all(row['dialog_attribution']['source'] == 'TeamComms' for row in result['recent_entries'])
        request = factory.get('/tjai/api/dashboard/search', {
            'q': marker, 'view': 'dialog', 'people': collaborator['operator_id'],
            'teamcomms': '1', 'expand_dialog': '1',
        })
        response = dashboard_search.__wrapped__(request)
        assert response.status_code == 200, response.content
        assert {row['id'] for row in json.loads(response.content)['entries']} == set(projection_ids[:3])
        Entry.objects.filter(pk=projection_ids[0]).update(deleted_at=1793134801)
        assert echo.id not in set(dialog_population(Entry.objects.filter(id__in=ids)).values_list('id', flat=True))
        transaction.set_rollback(True)
    print('PASS: both directions, reply-linked people, unrelated-session exclusion, source toggle, '
          'identity fallback, deduplication, counts, status and search; all writes rolled back')


if __name__ == '__main__':
    main()
