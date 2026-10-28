#!/usr/bin/env python3
"""Inspect the deployed Dialog page without changing entries.

Run with /home/admin/tools/playwright/python. A temporary personal web
session is removed on exit. --expect-readable checks the conversation
layout; --screenshot saves the inspected viewport locally.
"""
import argparse
import json
import subprocess

from playwright.sync_api import sync_playwright


def django(code):
    result = subprocess.run(
        ['/var/www/tjai/.venv/bin/python', '/var/www/tjai/manage.py', 'shell', '-c', code],
        cwd='/var/www/tjai', capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--people', required=True)
    parser.add_argument('--screenshot')
    parser.add_argument('--expect-readable', action='store_true')
    args = parser.parse_args()
    cookie = django('''
import json
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
assert user, 'No active personal administrator'
session = SessionStore()
session['_auth_user_id'] = str(user.pk)
session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
session['_auth_user_hash'] = user.get_session_auth_hash()
session.save()
print(json.dumps({'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key,
                  'domain': 'etaverse.com', 'path': settings.SESSION_COOKIE_PATH or '/'}))
''')
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 1100})
            context.add_cookies([cookie])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('https://etaverse.com/tjai/dashboard/?view=dialog&people=' + args.people)
            page.locator('#entries-list .entry').first.wait_for(timeout=30000)
            rows = page.locator('#entries-list .entry')
            first = rows.first
            result = {'rows': rows.count(), 'first_message': first.inner_text()[:1800],
                      'visible_metadata_badges': first.locator('.entry-badge:visible').count(),
                      'browser_errors': errors}
            if args.expect_readable:
                assert not errors, errors
                assert first.locator('.dialog-body').count() == 1
                assert len(first.locator('.dialog-body').inner_text()) > 100
                assert first.locator('.entry-badge:visible').count() == 0
                details = first.locator('details.dialog-details')
                assert details.count() == 1 and details.get_attribute('open') is None
                details.locator('summary').click()
                assert first.locator('.entry-badge:visible').count() > 0
                assert 'style="color:' not in first.inner_text(), 'Reference links corrupted metadata text'
                details.locator('summary').click()
                page.locator('#dialog-filter-options > summary').click()
                page.locator('#expand-toggle').uncheck()
                page.wait_for_url('**expand_dialog=0**')
                page.reload()
                page.locator('#entries-list .entry').first.wait_for(timeout=30000)
                assert not page.locator('#expand-toggle').is_checked()
                page.locator('#expand-toggle').check()
                page.wait_for_function("document.querySelector('#entries-list .dialog-body') !== null")
                linked = page.evaluate('''() => {
                    const text = '{"entry_id":"06f05d77-5170-4593-9403-faf6a9b2331a"}';
                    const target = document.createElement('div');
                    target.innerHTML = linkifyEntryReferences(escapeHtml(text));
                    return {text: target.textContent, expected: text,
                            anchors: target.querySelectorAll('a').length,
                            nested: target.querySelectorAll('a a').length};
                }''')
                assert linked['text'] == linked['expected'] and linked['anchors'] == 1 and not linked['nested'], linked
                page.locator('#dialog-filter-options > summary').click()
            if args.screenshot:
                page.screenshot(path=args.screenshot)
            print(json.dumps(result))
            browser.close()
    finally:
        django('from django.contrib.sessions.models import Session; '
               'Session.objects.filter(session_key=' + repr(cookie['value']) + ').delete(); '
               'print("null")')


if __name__ == '__main__':
    main()
