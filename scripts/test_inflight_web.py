#!/usr/bin/env python3
"""End-to-end test of the inflight surfaces against the live database
(docs/inflight.md): pages, item actions, state poll, Capcom shelf, and the
editor's three-way merge. Runs as a logged-in superuser through Django's
test client. Uses the entry given as argv[1] (entry_id or UUID) and
restores its content afterwards. Exit 0 on pass."""
import json
import sys
import time

import bootstrap  # noqa: F401 - Django setup
from django.contrib.auth import get_user_model
from django.test import Client

from tjai_app import inflight
from tjai_app.models import Entry, EntryVersion

ref = sys.argv[1] if len(sys.argv) > 1 else 'inflight-todos-build'
failures = []


def check(label, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + label + (('\n     ' + str(detail)[:300]) if (detail and not cond) else ''))
    if not cond:
        failures.append(label)


User = get_user_model()
user = User.objects.filter(is_superuser=True).order_by('id').first()
assert user, 'no superuser'
c = Client(HTTP_HOST='etaverse.com')
c.force_login(user)

entry = Entry.objects.filter(data__entry_id=ref, deleted_at__isnull=True).first() or Entry.objects.filter(id=ref).first()
assert entry, f'entry {ref} not found'
original = entry.content
eid = str(entry.id)

r = c.get('/inflight/')
check('index page renders', r.status_code == 200 and b'Inflight' in r.content, r.status_code)
r = c.get('/api/inflight/list')
d = r.json()
check('list API includes the entry', r.status_code == 200 and any(i['id'] == eid for i in d['items']), d)
r = c.get(f'/inflight/{ref}/')
check('live view renders', r.status_code == 200 and b'Live' in r.content, r.status_code)
r = c.get(f'/api/inflight/{ref}/state')
st = r.json()
check('state API', r.status_code == 200 and st['changed'] and st['open_count'] == len(inflight.parse(original)['live']), st.get('error'))
ts0 = st['modified_ts']
r = c.get(f'/api/inflight/{ref}/state?since={ts0}')
check('state since unchanged is cheap', r.json().get('changed') is False)

item = f'web test item {int(time.time())}'
r = c.post(f'/api/inflight/{ref}/item', data=json.dumps({'action': 'add', 'text': item}), content_type='application/json')
d = r.json()
check('add item', r.status_code == 200 and any(i['text'] == item for i in d['live']), d.get('error'))
ts1 = d['modified_ts']
r = c.get(f'/api/inflight/{ref}/state?since={ts0}')
check('state since reports the change', r.json().get('changed') is True and r.json()['modified_ts'] == ts1)
r = c.post(f'/api/inflight/{ref}/item', data=json.dumps({'action': 'done', 'text': item}), content_type='application/json')
d = r.json()
check('mark done', r.status_code == 200 and any(i['text'] == item for i in d['done']) and not any(i['text'] == item for i in d['live']), d.get('error'))
r = c.post(f'/api/inflight/{ref}/item', data=json.dumps({'action': 'reopen', 'text': item}), content_type='application/json')
d = r.json()
check('reopen', r.status_code == 200 and any(i['text'] == item for i in d['live']), d.get('error'))
r = c.post(f'/api/inflight/{ref}/item', data=json.dumps({'action': 'done', 'text': 'no such item'}), content_type='application/json')
check('unknown item is 409', r.status_code == 409)
v = EntryVersion.objects.filter(entry_id=eid).order_by('-timestamp').first()
check('item action attributed', v is not None and v.changed_by.startswith('inflight:'), v.changed_by if v else None)

r = c.get('/api/capcom/feed')
d = r.json()
check('capcom feed carries inflight shelf', r.status_code == 200 and any(i['id'] == eid for i in d.get('inflight', [])))

r = c.get(f'/entry/?uuid={eid}')
check('entry page shows live view link', r.status_code == 200 and b'live view' in r.content and b'inflightBanner' in r.content)

# editor merge: an "editor" loaded at ts_open, a session then flips the test item done, editor saves with a new item
entry.refresh_from_db()
ts_open = entry.timestamp_modified
base_loaded = entry.content
editor_text = inflight.add_item(entry.content, 'editor added this')
time.sleep(0.6)
r = c.post(f'/api/inflight/{ref}/item', data=json.dumps({'action': 'done', 'text': item}), content_type='application/json')
check('session change after editor opened', r.status_code == 200)
r = c.post(f'/api/entry/{eid}/save', data=json.dumps({'content': editor_text, 'expected_ts': ts_open, 'base_hash': inflight.fnv1a(base_loaded)}), content_type='application/json')
d = r.json()
entry.refresh_from_db()
p = inflight.parse(entry.content)
check('editor save merged three-way', r.status_code == 200 and d.get('merged') and not d.get('merge_conflict')
      and any(i['text'] == 'editor added this' for i in p['live']) and any(i['text'] == item for i in p['done'])
      and not any(i['text'] == item for i in p['live']), entry.content[-400:])

# conflict: both sides change the same description line
entry.refresh_from_db()
ts_open = entry.timestamp_modified
base_loaded = entry.content
first_desc_line = entry.content.split('\n')[2]
editor_text = entry.content.replace(first_desc_line, first_desc_line + ' (editor)', 1)
time.sleep(0.6)
from tjai_app import services
services._edit_entry_impl(entry_id=eid, content=entry.content.replace(first_desc_line, first_desc_line + ' (session)', 1), source='test')
r = c.post(f'/api/entry/{eid}/save', data=json.dumps({'content': editor_text, 'expected_ts': ts_open, 'base_hash': inflight.fnv1a(base_loaded)}), content_type='application/json')
d = r.json()
entry.refresh_from_db()
check('same-line edits surface as a conflict', d.get('merge_conflict') is True and '<<<<<<< yours' in entry.content and '>>>>>>> server' in entry.content, entry.content[:300])

# restore
services._edit_entry_impl(entry_id=eid, content=original, source='test-restore')
entry.refresh_from_db()
check('content restored', entry.content == original)
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
