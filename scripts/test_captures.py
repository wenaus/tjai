#!/usr/bin/env python3
"""Functionality test for captures (docs/addons.md): the add-capture
endpoint, file serving with bearer and session auth, the Captures page,
and delete. Runs through Django's test client against the live database;
the test entry and its files are removed afterwards. Exit 0 on pass."""
import struct
import sys
import zlib

import bootstrap  # noqa: F401 - Django setup
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from tjai_app import captures
from tjai_app.models import Entry, SysConfig

failures = []


def check(label, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + label + (('\n     ' + str(detail)[:300]) if (detail and not cond) else ''))
    if not cond:
        failures.append(label)


def png(w, h, rgb):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


key = SysConfig.objects.get(key='gmail_addon_api_key').value
bearer = {'HTTP_AUTHORIZATION': 'Bearer ' + key}
# A non-loopback address, so the bearer/session rules are enforced.
remote = Client(HTTP_HOST='etaverse.com', REMOTE_ADDR='203.0.113.5')

img1, img2 = png(40, 30, (200, 40, 40)), png(64, 64, (40, 120, 220))
r = remote.post('/api/add-capture', {
    'subject': 'test capture', 'sender': 'Tester <t@example.com>',
    'gmail_url': 'https://mail.google.com/mail/u/0/#inbox/abc',
    'note': 'a note', 'tags': 'testtag', 'context': 'tjai',
    'img0': SimpleUploadedFile('shot one.png', img1, content_type='image/png'),
    'img1': SimpleUploadedFile('Screenshot 2026-09-04.PNG', img2, content_type='image/png'),
}, **bearer)
check('add-capture 200', r.status_code == 200 and r.json().get('status') == 'ok', r.content[:200])
eid = r.json().get('entry_id')
entry = Entry.objects.filter(id=eid).first()
check('entry tagged capture, gmail, and the note tag', entry is not None
      and set(entry.tags.values_list('tag_name', flat=True)) >= {'capture', 'gmail', 'testtag'})
check('context applied', entry.context is not None and entry.context.name == 'tjai')
files = entry.data.get('files') or []
check('two files recorded with safe names', [f['name'] for f in files] == ['01-shot-one.png', '02-Screenshot-2026-09-04.png'], files)
check('files on disk', all((captures.ROOT / entry.data['capture_dir'] / f['name']).is_file() for f in files))
check('content: subject, sender, gmail link, note, two absolute image lines',
      entry.content.startswith('test capture from Tester <t@example.com> [gmail](')
      and 'a note' in entry.content and entry.content.count('![') == 2
      and f'{captures.SITE_URL}/capture/{eid}/01-shot-one.png' in entry.content, entry.content)

r = remote.get(f'/capture/{eid}/01-shot-one.png')
check('file without auth is 401', r.status_code == 401, r.status_code)
r = remote.get(f'/capture/{eid}/01-shot-one.png', **bearer)
check('file with bearer served as image/png', r.status_code == 200 and r['Content-Type'] == 'image/png'
      and b''.join(r.streaming_content) == img1, r.status_code)
check('unknown file 404', remote.get(f'/capture/{eid}/nope.png', **bearer).status_code == 404)
check('no files rejected 400', remote.post('/api/add-capture', {'subject': 'x'}, **bearer).status_code == 400)
r = remote.post('/api/add-capture', {'subject': 'x', 'f': SimpleUploadedFile('a.txt', b'hello', content_type='text/plain')}, **bearer)
check('non-image rejected 400', r.status_code == 400 and 'unsupported' in r.json().get('error', ''), r.content[:200])
check('post without bearer 401', remote.post('/api/add-capture', {'subject': 'x'}).status_code == 401)

user = get_user_model().objects.filter(is_superuser=True).order_by('id').first()
web = Client(HTTP_HOST='etaverse.com', REMOTE_ADDR='203.0.113.5')
web.force_login(user)
eid_js = eid.replace('-', '\\u002D').encode()  # the page's JSON goes through escapejs, which rewrites hyphens
r = web.get('/captures/')
check('captures page lists the capture', r.status_code == 200 and b'test capture' in r.content and eid_js in r.content, r.status_code)
check('file with session served', web.get(f'/capture/{eid}/02-Screenshot-2026-09-04.png').status_code == 200)
r = web.post(f'/api/capture/{eid}/file/01-shot-one.png/delete')
check('image delete 200, one left', r.status_code == 200 and r.json().get('remaining') == 1, r.content[:200])
entry.refresh_from_db()
check('image gone from disk, data, and content',
      not (captures.ROOT / entry.data['capture_dir'] / '01-shot-one.png').exists()
      and [f['name'] for f in entry.data['files']] == ['02-Screenshot-2026-09-04.png']
      and entry.content.count('![') == 1 and '01-shot-one' not in entry.content, entry.content)
check('unknown image 404', web.post(f'/api/capture/{eid}/file/nope.png/delete').status_code == 404)
check('captures page needs login', remote.get('/captures/').status_code == 302)
check('delete needs login', remote.post(f'/api/capture/{eid}/delete').status_code == 302)
r = web.post(f'/api/capture/{eid}/delete')
check('delete 200', r.status_code == 200 and r.json().get('status') == 'ok', r.content[:200])
entry.refresh_from_db()
check('entry in trash', entry.deleted_at is not None)
check('files removed', not (captures.ROOT / entry.data['capture_dir']).exists())
check('file after delete 404', remote.get(f'/capture/{eid}/01-shot-one.png', **bearer).status_code == 404)
check('deleted capture gone from the page', eid_js not in web.get('/captures/').content)

entry.tags.all().delete()
entry.versions.all().delete()
entry.delete()

print(f'\n{len(failures)} failure(s)' if failures else '\nall passed')
sys.exit(1 if failures else 0)
