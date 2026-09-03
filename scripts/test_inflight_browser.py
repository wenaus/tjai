#!/usr/bin/env python3
"""Real-browser check of the Capcom inflight view (docs/inflight.md): as the
first superuser, add an item through the MCP tool, click its done button
inside the frame, then reopen it, and remove it afterwards. Exercises CSRF,
the frame, and the live re-render, which the test-client test cannot.

Run with the playwright venv python (computers/common/setup-playwright-venv.sh):
    /home/admin/tools/playwright/python scripts/test_inflight_browser.py [entry_id]
"""
import json
import os
import subprocess
import sys
import time

TJAI = '/var/www/tjai'
HERE = os.path.dirname(os.path.abspath(__file__))
ref = sys.argv[1] if len(sys.argv) > 1 else 'inflight-todos-build'
item = f'browser click test {int(time.time())}'
failures = []


def check(label, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + label + (('\n     ' + str(detail)[:300]) if (detail and not cond) else ''))
    if not cond:
        failures.append(label)


def mcp(tool, args):
    r = subprocess.run([sys.executable, f'{HERE}/mcp_call.py', tool, json.dumps(args)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{tool} failed: {r.stdout[-300:]} {r.stderr[-300:]}')
    return json.loads(r.stdout)


def session():
    mk = subprocess.run([f'{TJAI}/.venv/bin/python', f'{TJAI}/manage.py', 'shell', '-c', '''
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
u = get_user_model().objects.filter(is_superuser=True).order_by('id').first()
s = SessionStore(); s['_auth_user_id'] = str(u.pk); s['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
s['_auth_user_hash'] = u.get_session_auth_hash(); s.save()
print(settings.SESSION_COOKIE_NAME, s.session_key, settings.SESSION_COOKIE_PATH or '/')
'''], capture_output=True, text=True, cwd=TJAI)
    return [l for l in mk.stdout.splitlines() if l.strip()][-1].split()


mcp('inflight_item', {'entry_id': ref, 'action': 'add', 'text': item})
cookie_name, session_key, cookie_path = session()
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1600, 'height': 1000})
        ctx.add_cookies([{'name': cookie_name, 'value': session_key, 'domain': 'etaverse.com', 'path': cookie_path}])
        page = ctx.new_page()
        page.goto(f'https://etaverse.com/tjai/capcom/?view=inflight&id={ref}', wait_until='networkidle')
        frame = page.frame_locator('#inflight-frame')
        btn = frame.locator(f'button[data-action="done"][data-text="{item}"]')
        check('item visible in frame with done button', btn.count() == 1, btn.count())
        btn.click()
        page.wait_for_timeout(2000)
        err = frame.locator('#err').inner_text().strip()
        check('done click produced no error', err == '', err)
        check('item moved to Done', frame.locator(f'#done button[data-action="reopen"][data-text="{item}"]').count() == 1)
        frame.locator(f'button[data-action="reopen"][data-text="{item}"]').click()
        page.wait_for_timeout(2000)
        err = frame.locator('#err').inner_text().strip()
        check('reopen click produced no error', err == '', err)
        check('item back in Live', frame.locator(f'#live button[data-action="done"][data-text="{item}"]').count() == 1)
        browser.close()
finally:
    subprocess.run([f'{TJAI}/.venv/bin/python', f'{TJAI}/manage.py', 'shell', '-c',
                    f"from django.contrib.sessions.models import Session; Session.objects.filter(session_key='{session_key}').delete()"],
                   capture_output=True, text=True, cwd=TJAI)
    r = subprocess.run([sys.executable, f'{HERE}/mcp_call.py', 'get_entry_by_entry_id', json.dumps({'entry_id': ref})], capture_output=True, text=True)
    content = json.loads(r.stdout).get('content', '') if r.returncode == 0 else ''
    for line in (f'- {item}', f'. {item}'):
        if line in content:
            mcp('replace_text_in_entry', {'entry_id': json.loads(r.stdout)['id'], 'old_text': line + '\n', 'new_text': ''})
            print('cleanup: removed test item')
            break
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
