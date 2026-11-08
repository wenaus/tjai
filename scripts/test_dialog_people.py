#!/usr/bin/env python3
"""Check the dialog source/identity boundary in one rolled-back transaction."""

import json
import uuid
import argparse
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

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
from tjai_app.teamcomms_transcript import directory_identities, event_entry_id, project_event
import sync_teamcomms_dialog as transcript_sync


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
        incoming = message(collaborator, 'question\n\n**Message body**')
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
        shown = next(row for row in result['recent_entries'] if row['metadata']['message_id'] == incoming['message_id'])
        assert shown['content'] == incoming['content'], 'Dialog default discarded the message body'
        assert '<strong>Message body</strong>' in shown['content_html']
        assert shown['dialog_attribution']['speaker'] == "Collaborator's AI"
        assert result['dialog_coverage'] == {'human_turns': 0, 'teamcomms_messages': 3}
        request = factory.get('/tjai/api/dashboard/status', {
            'view': 'dialog', 'people': collaborator['operator_id'], 'expand_dialog': '0',
        })
        compact = json.loads(dashboard_status.__wrapped__(request).content)['recent_entries']
        assert all(row['content_html'] is None and '\n' not in row['content'] for row in compact)
        request = factory.get('/tjai/api/dashboard/search', {
            'q': marker, 'view': 'dialog', 'people': collaborator['operator_id'],
            'teamcomms': '1', 'expand_dialog': '1',
        })
        response = dashboard_search.__wrapped__(request)
        assert response.status_code == 200, response.content
        assert {row['id'] for row in json.loads(response.content)['entries']} == set(projection_ids[:3])
        Entry.objects.filter(pk=projection_ids[0]).update(deleted_at=1793134801)
        assert echo.id not in set(dialog_population(Entry.objects.filter(id__in=ids)).values_list('id', flat=True))

        directory = []
        for ai in (owner, collaborator):
            directory.extend([
                {'participant_id': ai['participant_id'], 'name': ai['participant_name'],
                 'kind': 'ai', 'operator_id': ai['operator_id']},
                {'participant_id': ai['operator_id'], 'name': ai['operator_name'],
                 'kind': 'human', 'operator_id': None}])
        identities = directory_identities(directory)

        def event(number, role='assistant', ai=collaborator):
            text = marker + ' shared ' + str(number)
            return {'event_id': number, 'session_id': str(uuid.uuid4()),
                    'native_id': str(uuid.uuid4()), 'source_id': 'native:' + str(number),
                    'source_sequence': number, 'participant_id': ai['participant_id'],
                    'occurred_at': '2026-10-27T21:00:00+00:00',
                    'content': text, 'content_length': len(text), 'truncated': False,
                    'role': role, 'phase': 'message' if role == 'human' else 'final',
                    'host': 'remote-test', 'client': 'claude', 'topic': '',
                    'attribution': {'recorder_id': ai['participant_id'],
                                    'participant_id': ai['operator_id'] if role == 'human' else ai['participant_id']}}

        human_event, ai_event = event(91, 'human'), event(92)
        for shared in (human_event, ai_event):
            assert project_event(source, shared, identities, owner['operator_id'])
            assert not project_event(source, shared, identities, owner['operator_id'])
            stored = Entry.objects.get(pk=event_entry_id(source, shared['event_id']))
            assert stored.data['role'] == 'peer', 'Shared capture became a personal instruction'
            assert collaborator['operator_id'] in stored.data['dialog_people']
        assert not project_event(source, event(93, ai=owner), identities, owner['operator_id'])
        try:
            project_event(source, {**event(94), 'truncated': True}, identities, owner['operator_id'])
        except ValueError:
            pass
        else:
            raise AssertionError('Shortened shared content was accepted as complete')
        refresh_exchange_people()  # Shared transcript rows cannot break message reply grouping.
        response = dashboard_status.__wrapped__(factory.get('/tjai/api/dashboard/status', {
            'view': 'dialog', 'people': collaborator['operator_id'], 'teamcomms': '1'}))
        result = json.loads(response.content)
        assert result['dialog_coverage']['human_turns'] == 1
        assert {event_entry_id(source, 91), event_entry_id(source, 92)} <= {
            row['id'] for row in result['recent_entries']}

        batches = {
            None: {'events': [event(103), event(102)], 'next_before_id': 102},
            102: {'events': [event(101), event(100)], 'next_before_id': 100},
        }
        complete_103 = deepcopy(batches[None]['events'][0])
        batches[None]['events'][0].update(content='short', truncated=True)

        def call(_args, tool, query):
            if tool == 'list_participants':
                return {'participants': directory, 'next_offset': None}
            if query.get('event_id'):
                return {'events': [complete_103]}
            return {**batches[query.get('before_id')], 'coverage': {'complete': False}}

        state = {'local_person_id': owner['operator_id'], 'transcript_cursor': 100}
        args = SimpleNamespace(max_pages=1)
        with mock.patch.object(transcript_sync, 'call', side_effect=call), \
                mock.patch.object(transcript_sync, 'save_state'):
            added, state = transcript_sync.sync_transcripts(args, source, state)
            assert added == 2 and state['transcript_cursor'] == 100
            assert state['transcript_before'] == 102 and state['transcript_pending_head'] == 103
            added, state = transcript_sync.sync_transcripts(args, source, state)
            assert added == 1 and state['transcript_cursor'] == 103 and state['transcript_complete']
            late = event(104)
            late['occurred_at'] = '2020-01-01T00:00:00+00:00'
            batches[None] = {'events': [late, complete_103], 'next_before_id': 103}
            added, state = transcript_sync.sync_transcripts(args, source, state)
            assert added == 1 and state['transcript_cursor'] == 104
        transaction.set_rollback(True)
    print('PASS: both directions, reply-linked people, unrelated-session exclusion, source toggle, '
          'identity fallback, deduplication, counts, status and search; shared transcript attribution, '
          'complete content and resumable publication-order reads; all writes rolled back')


def check_served():
    """Read the deployed page/API with a temporary session; no entry writes."""
    import requests
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.backends.db import SessionStore

    state = json.loads(SysConfig.objects.get(key=SYNC_KEY).value)
    assert state['complete'] and not state.get('error'), 'History has not finished importing'
    user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    assert user is not None, 'No active personal administrator'
    session = SessionStore()
    session['_auth_user_id'] = str(user.pk)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = user.get_session_auth_hash()
    session.save()
    try:
        with requests.Session() as web:
            web.cookies.set(settings.SESSION_COOKIE_NAME, session.session_key,
                            domain='etaverse.com', path=settings.SESSION_COOKIE_PATH or '/')

            def get(path, params=None):
                response = web.get('https://etaverse.com/tjai/' + path, params=params, timeout=30)
                response.raise_for_status()
                assert '/login/' not in response.url, 'Deployed check reached login'
                return response

            page = get('dashboard/', {'view': 'dialog', 'teamcomms': '1'})
            assert 'id="teamcomms-only"' in page.text and 'class="person-link"' in page.text
            all_rows = get('api/dashboard/status', {'view': 'dialog', 'teamcomms': '1'}).json()
            selected = next(p for p in all_rows['dialog_people'] if p['name'] == 'Sakib Rahman')
            params = {'view': 'dialog', 'teamcomms': '1', 'people': selected['id']}
            result = get('api/dashboard/status', params).json()
            rows = result['recent_entries']
            assert rows, 'Selected person has no displayed messages'
            messages = [row['metadata']['message_id'] for row in rows]
            assert len(set(messages)) == len(messages), 'Duplicate broker messages in served view'
            assert all(selected['id'] in row['metadata']['dialog_people'] for row in rows)
            sent = sum(row['metadata']['viewer_relation']['sent'] for row in rows)
            received = sum(row['metadata']['viewer_relation']['received'] for row in rows)
            assert sent and received, 'Served view is missing one side'
            assert all(row['dialog_attribution']['source'] == 'TeamComms' for row in rows)
            counts = get('api/dialog/daily-counts', params).json()['daily_counts']
            assert sum(day['count'] for day in counts) >= len(rows)
            print(f'PASS: deployed page, person/source filtering, {len(rows)} unique messages '
                  f'({sent} sent, {received} received), attribution and counts')
    finally:
        session.delete()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--served', action='store_true', help='Read the deployed page and connected history')
    args = parser.parse_args()
    check_served() if args.served else main()
