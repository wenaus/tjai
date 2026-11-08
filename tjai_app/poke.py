"""Shared, versioned Poke records for the browser and MCP (docs/poke.md)."""
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import quote, urlsplit

from django.db import connection, transaction
from django.db.models import Q

from .models import Entry
from .tjai_utils import get_app_tz


LIVE = {'open', 'approved', 'deferred'}
STATES = LIVE | {'picked_up', 'done', 'dismissed'}
PUBLIC_ROOT = 'https://etaverse.com/tjai'


def _error(message, code='BAD_REQUEST', **extra):
    return {'error': message, 'code': code, **extra}


def _query():
    return Entry.objects.filter(kind='memory', deleted_at__isnull=True, data__poke__isnull=False)


def attention_count():
    return _query().filter(
        Q(data__poke__state='approved')
        | Q(data__poke__state='open', data__poke__category='attention')
        | Q(data__poke__state='deferred', data__poke__defer_until__lte=time.time())
    ).count()


def _lookup(ref, lock=False):
    qs = _query()
    if lock:
        qs = qs.select_for_update()
    return qs.filter(Q(id=ref) | Q(data__entry_id=ref)).first()


def _payload(entry, now=None):
    now = now or time.time()
    data = entry.data['poke']
    title, _, explanation = entry.content.partition('\n')
    state = data['state']
    group = data['category']
    if state == 'deferred':
        group = 'attention' if data['defer_until'] <= now else 'deferred'
    elif state == 'approved':
        group = 'attention'
    elif state not in LIVE:
        group = 'closed'
    ref = entry.data['entry_id']
    return {
        'entry_id': ref, 'title': title, 'explanation': explanation.strip(),
        'owner': data['owner'], 'category': data['category'], 'group': group,
        'state': state, 'refs': data.get('refs', []), 'answer': data.get('answer', ''),
        'activity': data.get('activity', ''), 'decision_ref': data.get('decision_ref', ''),
        'defer_date': data.get('defer_date', ''),
        'modified_ts': entry.timestamp_modified,
        'age_days': max(0, int((now - entry.timestamp_created) / 86400)),
        'keep_days': max(0, int((now - data.get('keep_since', entry.timestamp_created)) / 86400)),
        'url': f'{PUBLIC_ROOT}/poke/{quote(ref, safe="")}/',
        'frame_url': f'/tjai/poke/{quote(ref, safe="")}/?embed=1',
        'entry_url': f'/tjai/entry/{quote(ref, safe="")}/',
    }


def get_pokes(entry_id=None, include_closed=False):
    """Read current items; age and due grouping are calculated without writes."""
    if entry_id:
        entry = _lookup(entry_id)
        return _payload(entry) if entry else _error('Poke not found', 'NOT_FOUND')
    now = time.time()
    rows, incoming, outgoing = [], 0, 0
    for entry in _query().order_by('timestamp_created'):
        data = entry.data['poke']
        for event in data.get('keep_events', []):
            if event['at'] >= now - 7 * 86400:
                incoming += event['direction'] == 'in'
                outgoing += event['direction'] == 'out'
        if include_closed or data['state'] in LIVE:
            rows.append(_payload(entry, now))
    return {'items': rows, 'keep_flow': {'in': incoming, 'out': outgoing, 'days': 7},
            'attention_count': sum(row['group'] == 'attention' for row in rows)}


def _revision(entry, expected):
    try:
        matches = float(expected) == entry.timestamp_modified
    except (ValueError, TypeError):
        matches = False
    if not matches:
        return _error('This poke changed. Read it again before saving.', 'CONFLICT', current=_payload(entry))


def _refs(refs):
    if not isinstance(refs, list) or len(refs) > 6:
        raise ValueError('Use at most six reference links.')
    result = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError('Each reference needs label and url.')
        label, url = ref.get('label', ''), ref.get('url', '')
        if not isinstance(label, str) or not isinstance(url, str):
            raise ValueError('Reference label and url must be text.')
        label, url = label.strip(), url.strip()
        parsed = urlsplit(url)
        if not label or len(label) > 80 or len(url) > 2048 or parsed.scheme not in ('https', 'http') or not parsed.netloc:
            raise ValueError('References need a short label and a complete http(s) URL.')
        result.append({'label': label, 'url': url})
    return result


def save_poke(entry_id, title, explanation, owner, category='attention', refs=None,
              expected_modified_ts=None, source='poke:mcp'):
    from . import services
    if not isinstance(entry_id, str) or not re.fullmatch(r'poke-[a-z0-9]+(?:-[a-z0-9]+)*', entry_id) or len(entry_id) > 100:
        return _error('Use a short readable identifier beginning poke-, with lowercase words separated by hyphens.')
    if not all(isinstance(value, str) for value in (title, explanation, owner)):
        return _error('Title, explanation and owner must be text.')
    title, explanation, owner = title.strip(), explanation.strip(), owner.strip()
    if not title or len(title) > 100 or '\n' in title:
        return _error('Use a single-line title, at most 100 characters.')
    if len(explanation) < 30 or len(explanation) > 900 or len(explanation.split()) > 100:
        return _error('Explain the situation and ask in 2–4 short sentences, at most 100 words and 900 characters.')
    if not owner or len(owner) > 100 or category not in ('attention', 'keep'):
        return _error('Name the responsible person/session and choose attention or keep.')
    try:
        links = _refs(refs or [])
    except ValueError as exc:
        return _error(str(exc))
    with transaction.atomic():
        # The data.entry_id index is not unique. Serialize competing creates too.
        key = int.from_bytes(hashlib.sha256(entry_id.encode()).digest()[:8], 'big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
        entry = Entry.objects.select_for_update().filter(data__entry_id=entry_id, deleted_at__isnull=True).first()
        now = time.time()
        if entry:
            if not isinstance(entry.data.get('poke'), dict) or entry.kind != 'memory':
                return _error('That identifier belongs to another entry.', 'CONFLICT')
            conflict = _revision(entry, expected_modified_ts)
            if conflict:
                return conflict
            info = dict(entry.data['poke'])
            if info['state'] not in LIVE:
                return _error('Reopen this poke before editing it.')
            if info['state'] != 'open' and category != info['category']:
                return _error('Use a poke action to change an approved or deferred item.')
            if info['state'] == 'approved' and (entry.content != title + '\n\n' + explanation
                                                 or links != info.get('refs', [])):
                info.update(state='open', decision_ref='', answer='')
            _keep_transition(info, category, now)
            info.update(owner=owner, refs=links)
            result = services._edit_entry_impl(entry.id, content=title + '\n\n' + explanation,
                                               data={'poke': info}, source=source, locked_entry=entry)
        else:
            if expected_modified_ts is not None:
                return _error('Poke not found; nothing was recreated.', 'NOT_FOUND')
            info = {'state': 'open', 'category': category, 'owner': owner, 'refs': links,
                    'keep_events': [], 'creator': source}
            if category == 'keep':
                info.update(keep_since=now, keep_events=[{'at': now, 'direction': 'in'}])
            result = services.create_entry(title + '\n\n' + explanation, kind='memory',
                                           status='active', data={'entry_id': entry_id, 'poke': info})
        if 'error' in result:
            return result
        return _payload(_lookup(entry_id))


def _keep_transition(info, category, now):
    if info['category'] != category:
        events = [event for event in info.get('keep_events', []) if event['at'] >= now - 7 * 86400]
        events.append({'at': now, 'direction': 'in' if category == 'keep' else 'out'})
        info.update(category=category, keep_events=events)
        if category == 'keep':
            info['keep_since'] = now


def poke_action(entry_id, action, expected_modified_ts, answer='', until=None,
                activity=None, decision_ref=None, human=False, source='poke:mcp'):
    from . import services
    if not isinstance(answer, str) or len(answer) > 400:
        return _error('Keep the response under 400 characters.')
    with transaction.atomic():
        entry = _lookup(entry_id, lock=True)
        if not entry:
            return _error('Poke not found', 'NOT_FOUND')
        conflict = _revision(entry, expected_modified_ts)
        if conflict:
            return conflict
        info, now = dict(entry.data['poke']), time.time()
        if action not in ('attention', 'keep', 'defer', 'approve', 'pickup', 'done', 'dismiss', 'reopen'):
            return _error('Unknown poke action.')
        if info['state'] not in LIVE and action != 'reopen':
            return _error('Reopen this poke before changing it.')
        if action in ('done', 'dismiss') and not human and not answer.strip():
            return _error('Give the short reason this poke is completed or no longer needed.')
        if action == 'approve':
            if not human:
                from .comms_dialog import external_peer_data
                from .dialog_context import DIALOG_CONTEXTS
                from .dialog import HARNESS_PREFIXES
                turn = Entry.objects.filter(Q(id=decision_ref) | Q(data__entry_id=decision_ref), deleted_at__isnull=True).first() if decision_ref else None
                data = turn.data if turn and isinstance(turn.data, dict) else {}
                if (not turn or turn.context_id not in DIALOG_CONTEXTS or data.get('role') != 'user'
                        or external_peer_data(turn.content) is not None
                        or turn.content.startswith(HARNESS_PREFIXES + ('[TJAI peer ',
                            'Another Claude session sent a message:', 'Peer communication from another session'))):
                    return _error('Approval needs the original human dialog entry, not a peer message.')
            info.update(state='approved', decision_ref=decision_ref or 'web')
            _keep_transition(info, 'attention', now)
        elif action == 'pickup':
            if info['state'] != 'approved':
                return _error('Only an approved poke can be picked up.')
            target = Entry.objects.filter(kind='todo', status='inflight', deleted_at__isnull=True).filter(
                Q(id=activity) | Q(data__entry_id=activity)).first() if activity else None
            if not target:
                return _error('Pickup needs an existing open Inflight activity.')
            info.update(state='picked_up', activity=target.data.get('entry_id') or target.id)
        elif action == 'defer':
            try:
                due = datetime.strptime(until, '%Y-%m-%d').replace(tzinfo=get_app_tz()).timestamp()
            except (ValueError, TypeError):
                return _error('Choose a future date as YYYY-MM-DD.')
            if due <= now:
                return _error('Choose a future date.')
            _keep_transition(info, 'attention', now)
            info.update(state='deferred', defer_until=due, defer_date=until)
        elif action in ('attention', 'keep', 'reopen'):
            if info['state'] == 'approved' and not human:
                return _error('An approved item needs pickup or an explicit operator decision.')
            info['state'] = 'open'
            _keep_transition(info, 'attention' if action == 'reopen' else action, now)
        else:
            if info['category'] == 'keep':
                _keep_transition(info, 'attention', now)
            info['state'] = 'done' if action == 'done' else 'dismissed'
        info['answer'] = answer.strip() or info.get('answer', '')
        result = services._edit_entry_impl(entry.id, data={'poke': info},
                    status='archive' if info['state'] not in LIVE else 'active', source=source, locked_entry=entry)
        return result if 'error' in result else _payload(entry)
