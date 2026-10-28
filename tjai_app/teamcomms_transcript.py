"""Read-side projection of explicitly shared TeamComms transcript events.

Sharing and consent are enforced by the broker/recorder. This consumer reads
the authorized shared endpoint and never changes another session's policy.
Recorded human text remains peer material, separate from personal instructions.
"""

import time
import uuid
from datetime import datetime

from django.db import transaction

from .dialog_context import TEAMCOMMS_DIALOG_CONTEXT, TEAMCOMMS_TRANSCRIPT_TAG
from .models import Context, Entry, Tag
from .teamcomms_dialog import person, speaker


def directory_identities(participants):
    """Resolve operator names only from the authenticated directory."""
    by_id = {str(uuid.UUID(row['participant_id'])): row for row in participants}
    result = {}
    for participant_id, row in by_id.items():
        operator = by_id.get(row.get('operator_id'))
        if operator is not None and operator['kind'] != 'human':
            raise ValueError('TeamComms operator is not a human directory member')
        result[participant_id] = {
            'participant_id': participant_id, 'participant_name': row['name'],
            'participant_kind': row['kind'],
            'operator_id': operator['participant_id'] if operator else None,
            'operator_name': operator['name'] if operator else None,
        }
    return result


def event_entry_id(source, event_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f'tjai-teamcomms-transcript-v1:{source}:{event_id}'))


def project_event(source, event, identities, local_person_id):
    """Keep one complete attributed remote turn; retain personal capture as-is."""
    if event['role'] not in {'human', 'assistant'}:
        return False
    event_id = event['event_id']
    if type(event_id) is not int or event_id < 1:
        raise ValueError('TeamComms transcript has an invalid event identity')
    content = event['content']
    if (event.get('truncated') or not isinstance(content, str)
            or len(content) != event['content_length']):
        raise ValueError('A shortened transcript event cannot be stored as complete')
    recorder = identities[event['participant_id']]
    # Personal native capture already supplies these turns. Import remote
    # collaborators through the shared endpoint without duplicating it.
    if person(recorder)[0] == local_person_id:
        return False
    recorded = event['attribution']
    if recorded.get('recorder_id') != event['participant_id']:
        raise ValueError('Transcript recorder attribution does not match its session')
    if event['role'] == 'human':
        human = identities.get(recorded.get('participant_id'))
        if human and human['participant_kind'] != 'human':
            raise ValueError('Transcript human attribution names a nonhuman participant')
        author = human
        if author:
            label = speaker(author) + ' (recorded)'
        else:
            label = 'Unverified human (recorded)'
    else:
        if recorded.get('participant_id') != event['participant_id']:
            raise ValueError('Transcript assistant attribution does not match its session')
        author = recorder
        label = speaker(author)
    parties = dict([person(recorder)])
    if author:
        parties.update([person(author)])
    stamp = datetime.fromisoformat(event['occurred_at'].replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('TeamComms transcript time requires a timezone')
    data = {
        'entry_id': f'teamcomms-transcript-{event_id}',
        'role': 'peer', 'content_type': 'shared_transcript',
        'peer_source': 'teamcomms', 'dialog_projection': 'teamcomms-transcript',
        'peer_provenance': 'broker_shared_transcript', 'broker_source': source,
        'broker_recorded_at': time.time(), 'transcript_event_id': event_id,
        'recorded_role': event['role'], 'recorded_attribution': recorded,
        'author_identity': author, 'recorder_identity': recorder,
        'dialog_people': parties, 'dialog_speaker': label,
        'session_id': event['native_id'], 'sender_session_id': event['session_id'],
        'source_id': event['source_id'], 'source_sequence': event['source_sequence'],
        'hostname': event['host'], 'client': event['client'], 'phase': event['phase'],
        'topic': event.get('topic', ''),
    }
    now = time.time()
    with transaction.atomic():
        Context.objects.get_or_create(name=TEAMCOMMS_DIALOG_CONTEXT, defaults={
            'title': 'TeamComms dialog', 'timestamp_created': now, 'timestamp_modified': now})
        entry, created = Entry.objects.get_or_create(id=event_entry_id(source, event_id), defaults={
            'content': content, 'kind': 'memory', 'context_id': TEAMCOMMS_DIALOG_CONTEXT,
            'data': data, 'is_dirty': 0,
            'timestamp_created': stamp.timestamp(), 'timestamp_modified': stamp.timestamp()})
        if created:
            Tag.objects.create(entry=entry, tag_name=TEAMCOMMS_TRANSCRIPT_TAG)
        elif (entry.content != content or entry.timestamp_created != stamp.timestamp()
              or entry.data.get('recorded_role') != event['role']
              or entry.data.get('recorded_attribution') != recorded):
            raise ValueError(f'TeamComms transcript event changed: {event_id}')
    return created
