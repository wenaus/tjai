#!/usr/bin/env python3
"""Screenshot a tjai page as the first superuser, for AI visual checks.

Usage: screenshot_page.py <url> <out.png> [wait_ms]
Creates a Django session for the superuser and injects its cookie into a
playwright chromium context; the session is deleted afterwards. Run with
the playwright venv's python wrapper (computers/common/setup-playwright-venv.sh),
which does not carry Django: the Django side runs through the tjai venv.
"""
import os
import subprocess
import sys
import time

TJAI = '/var/www/tjai'
url, out = sys.argv[1], sys.argv[2]
wait_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

mk = subprocess.run([f'{TJAI}/.venv/bin/python', f'{TJAI}/manage.py', 'shell', '-c', '''
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
u = get_user_model().objects.filter(is_superuser=True).order_by('id').first()
s = SessionStore(); s['_auth_user_id'] = str(u.pk); s['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
s['_auth_user_hash'] = u.get_session_auth_hash(); s.save()
print(settings.SESSION_COOKIE_NAME, s.session_key, settings.SESSION_COOKIE_PATH or '/')
'''], capture_output=True, text=True, cwd=TJAI)
line = [l for l in mk.stdout.splitlines() if l.strip()][-1]
cookie_name, session_key, cookie_path = line.split()
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1600, 'height': 1000})
        ctx.add_cookies([{'name': cookie_name, 'value': session_key, 'domain': 'etaverse.com', 'path': cookie_path}])
        page = ctx.new_page()
        page.goto(url, wait_until='networkidle')
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=out, full_page=False)
        print('saved', out, 'title:', page.title())
        browser.close()
finally:
    subprocess.run([f'{TJAI}/.venv/bin/python', f'{TJAI}/manage.py', 'shell', '-c',
                    f"from django.contrib.sessions.models import Session; Session.objects.filter(session_key='{session_key}').delete()"],
                   capture_output=True, text=True, cwd=TJAI)
