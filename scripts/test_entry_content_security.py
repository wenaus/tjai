#!/usr/bin/env python3
"""Check entry-save access gates and both renderers without database access."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tjai_project.settings.base')
os.environ.setdefault('DJANGO_DATABASE_URL', 'postgresql://unused@localhost/unused')

import django
django.setup()

from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory
from tjai_app.views import api_entry_save, _render_markdown
from md_render import render_markdown

factory = RequestFactory(HTTP_HOST='localhost')
entry_id = uuid.uuid4()
url = f'/api/entry/{entry_id}/save'
for suffix in ('', '?beacon=1'):
    request = factory.post(url + suffix, data='{"content":"changed"}',
                           content_type='application/json')
    request.user = AnonymousUser()
    assert api_entry_save(request, entry_id).status_code == 302
request = factory.get(url)
request.user = SimpleNamespace(is_authenticated=True)
assert api_entry_save(request, entry_id).status_code == 405

csrf = CsrfViewMiddleware(lambda request: HttpResponse())
for suffix in ('', '?beacon=1'):
    request = factory.post(url + suffix, data='{"content":"changed"}',
                           content_type='application/json')
    request.user = SimpleNamespace(is_authenticated=True)
    assert csrf.process_view(request, api_entry_save, (), {'entry_id': entry_id}).status_code == 403
    request.META['HTTP_X_CSRFTOKEN'] = get_token(request)
    request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META['CSRF_COOKIE']
    assert csrf.process_view(request, api_entry_save, (), {'entry_id': entry_id}) is None

for render in (_render_markdown, render_markdown):
    html = render('<img src="/image.png" onerror="void(0)"> '
                  '<a href="jav&#x61;script:void(0)" onclick="void(0)">link</a> '
                  '<svg onload="void(0)"><a href="javascript:void(0)">svg</a></svg>')
    for unsafe in ('onerror', 'onclick', 'onload', 'javascript:', '<svg'):
        assert unsafe not in html, (render.__name__, unsafe)
    assert 'src="/image.png"' in html
    html = render('<details><summary>Details</summary><strong>body</strong></details>')
    assert '<details><summary>Details</summary><strong>body</strong></details>' in html
    html = render('| a | b |\n| :-- | --: |\n| 1 | 2 |')
    assert '<table>' in html and 'text-align:left' in html and 'text-align:right' in html
    html = render('```python\nprint("<img onerror=example>")\n```')
    assert 'class="language-python"' in html and '&lt;img onerror=example&gt;' in html

print('Entry-save authentication, POST/CSRF gates and both HTML sanitizers passed (no database access).')
