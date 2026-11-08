"""Personal browser projection of authorized canonical TeamComms messages.

The broker supplies authorship and fixed recipients. Transcript capture is
retained separately; importing a message never makes it an operator instruction.
"""

import time
import uuid
from datetime import datetime

from django.db import transaction

from .dialog_context import TEAMCOMMS_DIALOG_CONTEXT, TEAMCOMMS_DIALOG_TAG
from .models import Context, Entry, Tag


def message_entry_id(source, message_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f'tjai-teamcomms-message-v1:{source}:{uuid.UUID(message_id)}'))


def person(identity):
    """An operator when verified, otherwise the participant without guessing."""
    participant_id = str(uuid.UUID(identity['participant_id']))
    operator_id = identity.get('operator_id')
    if operator_id:
        if not identity.get('operator_name'):
            raise ValueError('TeamComms operator identity has no name')
        return str(uuid.UUID(operator_id)), identity['operator_name']
    name = identity.get('participant_name') or identity.get('name')
    if not name:
        raise ValueError('TeamComms participant identity has no name')
    return participant_id, name


def speaker(identity, session=None, local_person_id=None):
    person_id, name = person(identity)
    if person_id == local_person_id:
        name = 'Torre'
    kind = identity.get('participant_kind') or identity.get('kind') or 'unknown'
    label = f"{name}'s AI" if kind == 'ai' else f'{name} · {kind}'
    if session and session.get('name'):
        label += f' ({session["name"]})'
    return label


def project_message(source, message, author, recipients, sender_session=None, local_person_id=None):
    """Insert one canonical message. Retrying cannot add another dialog row."""
    message_id = str(uuid.UUID(message['message_id']))
    if message['author_id'] != author['participant_id']:
        raise ValueError('TeamComms author identity does not match the message')
    stamp = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('TeamComms message time requires a timezone')
    if not isinstance(message['content'], str) or not message['content'].strip():
        raise ValueError('TeamComms message has no content')
    reply_to = str(uuid.UUID(message['reply_to'])) if message.get('reply_to') else None
    identities = [author, *recipients]
    parties = dict(person(identity) for identity in identities)
    if local_person_id in parties:
        parties[local_person_id] = 'Torre'
    sender_session = sender_session or {}
    data = {
        'entry_id': f'teamcomms-{message_id}',
        'role': 'peer', 'content_type': 'peer_message',
        'dialog_projection': 'teamcomms', 'peer_source': 'teamcomms',
        'peer_provenance': 'broker_history', 'message_id': message_id,
        'reply_to': reply_to, 'topic': message.get('topic', ''),
        'broker_source': source, 'broker_recorded_at': time.time(),
        'peer_sender': message['publication_snapshot'], 'author_identity': author,
        'publication_snapshot': message['publication_snapshot'],
        'audience': message['audience'], 'viewer_relation': message['viewer_relation'],
        'sender_session_id': message.get('sender_session_id'),
        'recipient_identities': recipients, 'dialog_parties': parties,
        'dialog_people': parties,
        'dialog_speaker': speaker(author, sender_session, local_person_id),
        'dialog_audience': ', '.join(sorted({speaker(identity, local_person_id=local_person_id)
                                            for identity in recipients})),
        'client': sender_session.get('client') or 'teamcomms',
        'model': sender_session.get('model') or '',
        'hostname': sender_session.get('host') or (message['publication_snapshot'] or {}).get('host') or 'teamcomms',
        'session_id': sender_session.get('native_id') or '',
    }
    now = time.time()
    Context.objects.get_or_create(name=TEAMCOMMS_DIALOG_CONTEXT, defaults={
        'title': 'TeamComms dialog', 'timestamp_created': now, 'timestamp_modified': now,
    })
    with transaction.atomic():
        entry, created = Entry.objects.get_or_create(
            id=message_entry_id(source, message_id), defaults={
                'content': message['content'], 'kind': 'memory', 'is_dirty': 0,
                'context_id': TEAMCOMMS_DIALOG_CONTEXT, 'data': data,
                'timestamp_created': stamp.timestamp(), 'timestamp_modified': stamp.timestamp(),
            },
        )
        if created:
            Tag.objects.create(entry=entry, tag_name=TEAMCOMMS_DIALOG_TAG)
        elif (entry.content != message['content']
              or (entry.data or {}).get('message_id') != message_id
              or (entry.data or {}).get('reply_to') != reply_to
              or entry.timestamp_created != stamp.timestamp()):
            raise ValueError(f'TeamComms message changed: {message_id}')
    return created


def refresh_exchange_people():
    """Share people along visible reply links, never along an LLM session ID."""
    from .dialog_people import projection_entries
    rows = list(projection_entries().only('id', 'data'))
    by_message = {row.data['message_id']: row for row in rows}
    roots = {}

    def root(message_id):
        path, seen = [], set()
        while message_id not in roots:
            if message_id in seen:
                raise ValueError('TeamComms history contains a reply cycle')
            seen.add(message_id)
            path.append(message_id)
            parent = by_message[message_id].data.get('reply_to')
            if parent not in by_message:
                break
            message_id = parent
        result = roots.get(message_id, message_id)
        for item in path:
            roots[item] = result
        return result

    groups = {}
    for message_id, row in by_message.items():
        groups.setdefault(root(message_id), {}).update(row.data['dialog_parties'])
    changed = []
    for message_id, row in by_message.items():
        people = groups[root(message_id)]
        parent = by_message.get(row.data.get('reply_to'))
        parent_id = parent.data['entry_id'] if parent else None
        if row.data.get('dialog_people') != people or row.data.get('reply_entry_id') != parent_id:
            row.data = {**row.data, 'dialog_people': people, 'reply_entry_id': parent_id}
            changed.append(row)
    if changed:
        Entry.objects.bulk_update(changed, ['data'], batch_size=100)
    return len(rows)
