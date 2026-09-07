#!/usr/bin/env python3
"""A day's dialog, prepared for assessment: fetched, split by session,
classified, and packed into calls.

Every dialog turn carries the session_id that recorded it, so a day of
interleaved work across hosts and sessions separates by code before an
assessor reads any of it. A session is one of:

    session   a working session
    codex     a Codex research run: one prompt, one report
    headless  a prompt with no assistant turn (a cron or a `claude -p` run)
    replay    a session whose assistant turns are copies of an earlier
              session's, which the recorder writes when a thread is moved
              to background

Assessment reads session and codex; headless and replay are dropped.
Sessions are packed into calls no larger than a cap: a session over the
cap gets a call of its own, the rest pack together up to it, and a session
is never split. Design and the measurements behind the cap: docs/assessment.md.
"""
from collections import Counter, defaultdict

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry, Tag

# Measured on 2026-09-06: 1,480,333 chars of assessment prompt read as
# ~550K tokens.
CHARS_PER_TOKEN = 2.7
REPLAY_FRACTION = 0.8   # share of a session's assistant turns copied from an earlier session
ASSESSED_KINDS = ('session', 'codex')


def fetch_dialog(date_str):
    """All dialog turns for a date, ordered chronologically."""
    from datetime import datetime, timedelta
    from django.utils.timezone import make_aware
    from tjai_app.services import get_timezone

    tz = get_timezone()
    target = datetime.strptime(date_str, '%Y-%m-%d').date()
    next_day = target + timedelta(days=1)

    start_ts = make_aware(datetime.combine(target, datetime.min.time()), tz).timestamp()
    end_ts = make_aware(datetime.combine(next_day, datetime.min.time()), tz).timestamp()

    from tjai_app.dialog_context import DIALOG_TAG
    dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)

    entries = Entry.objects.filter(
        id__in=dialog_ids,
        deleted_at__isnull=True,
        timestamp_created__gte=start_ts,
        timestamp_created__lt=end_ts,
    ).order_by('timestamp_created')

    turns = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        ts = datetime.fromtimestamp(e.timestamp_created, tz).isoformat()
        turns.append({
            'id': str(e.id),
            'timestamp': ts,
            'role': data.get('role', 'unknown'),
            'client': data.get('client', ''),
            'model': data.get('model', ''),
            'hostname': data.get('hostname', ''),
            'session_id': data.get('session_id', ''),
            'content': e.content,
        })
    return turns


def format_turn(turn):
    """One dialog turn as the assessor reads it: a header carrying the time,
    role, host and session, client and model, and the entry UUID, then the
    content. The session lets a day of interleaved sessions be told apart;
    the UUID lets a scored event cite the turn it rests on, which the
    assessment log asks for."""
    role = turn['role'].upper()
    where = turn.get('hostname') or ''
    sid = (turn.get('session_id') or '')[:8]
    if sid:
        where = f"{where}/{sid}" if where else sid
    where = f" [{where}]" if where else ''
    model_parts = [p for p in (turn.get('client'), turn.get('model')) if p]
    model = f" ({' / '.join(model_parts)})" if model_parts else ''
    uid = f" {{{turn['id']}}}" if turn.get('id') else ''
    return f"### {turn['timestamp']} {role}{where}{model}{uid}\n{turn['content']}"


def _classify(sess, earlier_assistant_content):
    turns = sess['turns']
    if turns and all(t['client'] == 'codex' for t in turns):
        return 'codex'
    assistant = [t for t in turns if t['role'] == 'assistant']
    if not assistant:
        return 'headless'
    copied = sum(1 for t in assistant if t['content'] in earlier_assistant_content)
    if len(assistant) >= 3 and copied >= REPLAY_FRACTION * len(assistant):
        return 'replay'
    return 'session'


def split_sessions(turns):
    """The day's turns as sessions, in order of first turn, each classified
    and sized. A session dict carries session_id, host, client, kind, turns,
    user_turns, assistant_turns, first, last (HH:MM:SS), models, chars,
    est_tokens and label."""
    by_sid = {}
    for t in turns:
        sid = t.get('session_id') or 'nosession'
        s = by_sid.setdefault(sid, {'session_id': sid, 'host': t['hostname'], 'client': t['client'],
                                    'turns': [], 'models': Counter()})
        s['turns'].append(t)
        if t['model']:
            s['models'][t['model']] += 1

    # A replay is judged against the assistant content of every session that
    # started before it on the same host.
    sessions = sorted(by_sid.values(), key=lambda s: s['turns'][0]['timestamp'])
    seen_by_host = defaultdict(set)
    for s in sessions:
        s['kind'] = _classify(s, seen_by_host[s['host']])
        for t in s['turns']:
            if t['role'] == 'assistant':
                seen_by_host[s['host']].add(t['content'])
        s['user_turns'] = sum(1 for t in s['turns'] if t['role'] == 'user')
        s['assistant_turns'] = len(s['turns']) - s['user_turns']
        s['first'] = s['turns'][0]['timestamp'][11:19]
        s['last'] = s['turns'][-1]['timestamp'][11:19]
        s['chars'] = sum(len(format_turn(t)) + 2 for t in s['turns'])
        s['est_tokens'] = round(s['chars'] / CHARS_PER_TOKEN)
        s['models'] = dict(s['models'])
        s['label'] = (f"{s['host'] or 'nohost'}/{s['session_id'][:8]} {s['first'][:5]}–{s['last'][:5]}, "
                      f"{s['user_turns']} user and {s['assistant_turns']} assistant turns")
    return sessions


def pack_calls(sessions, cap_chars):
    """Sessions packed into calls of at most cap_chars: a session over the
    cap gets a call of its own, the rest are packed first-fit by size.
    Returns a list of packs, each a list of sessions in order of first
    turn; packs ordered the same way."""
    big = [s for s in sessions if s['chars'] > cap_chars]
    small = sorted((s for s in sessions if s['chars'] <= cap_chars), key=lambda s: -s['chars'])
    bins = []
    for s in small:
        for b in bins:
            if b['chars'] + s['chars'] <= cap_chars:
                b['sessions'].append(s)
                b['chars'] += s['chars']
                break
        else:
            bins.append({'sessions': [s], 'chars': s['chars']})
    packs = [[s] for s in big] + [b['sessions'] for b in bins]
    for p in packs:
        p.sort(key=lambda s: s['turns'][0]['timestamp'])
    packs.sort(key=lambda p: p[0]['turns'][0]['timestamp'])
    return packs


def pack_turns(pack):
    """The turns of a pack, session by session (a session is one stream;
    sessions are not interleaved), each session's turns in time order."""
    turns = []
    for s in pack:
        turns.extend(s['turns'])
    return turns
