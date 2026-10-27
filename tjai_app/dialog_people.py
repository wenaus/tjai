"""Shared browser-dialog population, person selection and source attribution."""

import json

from django.db.models import Count, DateTimeField, Func, Q
from django.db.models.functions import TruncDate

from .dialog_context import DIALOG_TAG, TEAMCOMMS_DIALOG_TAG
from .models import Entry, SysConfig, Tag
from .tjai_utils import get_app_tz

SYNC_KEY = 'teamcomms_dialog_sync'
LOCAL_PERSON = 'local'


def sync_state():
    raw = SysConfig.objects.filter(key=SYNC_KEY).values_list('value', flat=True).first()
    return json.loads(raw) if raw else {}


def projection_entries():
    return Entry.objects.filter(
        id__in=Tag.objects.filter(tag_name=TEAMCOMMS_DIALOG_TAG).values('entry_id'),
        deleted_at__isnull=True,
    )


def dialog_population(qs):
    """One browser row per broker message; native transcript records are retained."""
    ids = Tag.objects.filter(tag_name__in=(DIALOG_TAG, TEAMCOMMS_DIALOG_TAG)).values('entry_id')
    # Even an archived/trashed projection supersedes its transcript copies.
    projected = Entry.objects.filter(id__in=Tag.objects.filter(
        tag_name=TEAMCOMMS_DIALOG_TAG).values('entry_id')).values('data__message_id')
    native_copies = Entry.objects.filter(
        data__peer_source='teamcomms', data__message_id__in=projected,
    ).filter(Q(data__dialog_projection__isnull=True) | ~Q(data__dialog_projection='teamcomms'))
    return qs.filter(id__in=ids).filter(
        Q(data__role__isnull=True) | ~Q(data__role='peer_echo')
    ).exclude(id__in=native_copies.values('id'))


def apply_people_filters(qs, params, state=None):
    """People widen the selection; source selection intersects it before paging."""
    if params.get('teamcomms') == '1':
        qs = qs.filter(data__peer_source='teamcomms')
    people = [p for p in params.get('people', '').split(',') if p]
    if people:
        state = sync_state() if state is None else state
        owner = state.get('local_person_id', LOCAL_PERSON)
        match = Q(data__dialog_people__has_any_keys=people)
        if owner in people or LOCAL_PERSON in people:
            # The personal recorder's ordinary human/AI dialog belongs to its
            # operator. External envelopes have no such implicit attribution.
            match |= Q(data__peer_source__isnull=True) | ~Q(data__peer_source='teamcomms')
        qs = qs.filter(match)
    return qs


def dialog_options(state=None):
    state = sync_state() if state is None else state
    people = {state.get('local_person_id', LOCAL_PERSON): 'Torre'}
    for names in projection_entries().values_list('data__dialog_people', flat=True).distinct():
        people.update(names or {})
    people[state.get('local_person_id', LOCAL_PERSON)] = 'Torre'
    return [{'id': key, 'name': name} for key, name in
            sorted(people.items(), key=lambda item: item[1].casefold())]


def daily_counts(params, days=None):
    import time
    qs = apply_people_filters(dialog_population(
        Entry.objects.filter(deleted_at__isnull=True).exclude(status='archive')
    ), params)
    for param, key in (('machine', 'hostname'), ('client', 'client'), ('model', 'model')):
        if params.get(param):
            qs = qs.filter(**{f'data__{key}': params[param]})
    if days is not None:
        qs = qs.filter(timestamp_modified__gte=time.time() - days * 86400)
    rows = qs.annotate(day=TruncDate(
        Func('timestamp_modified', function='to_timestamp', output_field=DateTimeField()),
        tzinfo=get_app_tz(),
    )).values('day').annotate(count=Count('pk')).order_by('day')
    return [{'date': row['day'].isoformat(), 'count': row['count']} for row in rows]


def dialog_attribution(data):
    if data.get('dialog_projection') == 'teamcomms':
        return {
            'source': 'TeamComms', 'speaker': data['dialog_speaker'],
            'audience': data.get('dialog_audience', ''),
            'reply_entry_id': data.get('reply_entry_id'),
        }
    if data.get('peer_source') == 'teamcomms':
        sender = data.get('peer_sender') or {}
        return {'source': 'TeamComms', 'speaker': sender.get('name', 'Unknown sender'),
                'unverified': True}
    role = data.get('role')
    if role == 'user':
        return {'source': 'Dialog', 'speaker': 'Torre · human'}
    if role == 'assistant':
        return {'source': 'Dialog', 'speaker': (data.get('client') or 'AI') + ' · AI'}
    if role == 'peer':
        return {'source': 'TJAI', 'speaker': (data.get('peer_sender') or {}).get('name') or 'AI peer'}
    return {'source': 'Dialog', 'speaker': 'Harness' if role == 'harness' else 'Unknown'}
