"""Authenticated Poke pages; all mutations use the shared Poke writer."""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods
from urllib.parse import quote

from . import poke


def _reference_lines(text):
    return [{'label': parts[0].strip(), 'url': parts[-1].strip()}
            for line in text.splitlines() if line.strip()
            for parts in [line.split(' | ', 1)]]


@login_required
@xframe_options_exempt
@require_http_methods(['GET', 'POST'])
def poke_page(request, entry_id=None):
    if request.method == 'GET' and request.GET.get('embed') != '1':
        return redirect('/tjai/capcom/?view=poke' + ('&id=' + quote(entry_id, safe='') if entry_id else ''))
    error = ''
    form = request.POST if request.method == 'POST' else {}
    if request.method == 'POST':
        action = form.get('action')
        ref = entry_id or form.get('entry_id') or 'poke-' + slugify(form.get('title', ''))
        source = 'poke:web:' + request.user.username
        if action == 'save':
            result = poke.save_poke(ref, form.get('title', ''), form.get('explanation', ''),
                form.get('owner', ''), form.get('category', 'attention'),
                _reference_lines(form.get('refs', '')), form.get('modified_ts') or None, source)
        else:
            result = poke.poke_action(ref, action, form.get('modified_ts'),
                answer=form.get('answer', ''), until=form.get('until'),
                activity=form.get('activity'), human=True, source=source)
        if 'error' not in result:
            return redirect(result['frame_url'])
        error = result['error']
    data = poke.get_pokes(entry_id, include_closed=True)
    if 'error' in data:
        raise Http404(data['error'])
    selected = data if entry_id else None
    if selected:
        selected['reference_lines'] = '\n'.join(f"{r['label']} | {r['url']}" for r in selected['refs'])
    else:
        groups = []
        for key, label in [('attention', 'Needs you'), ('keep', 'Keep'), ('deferred', 'Later')]:
            groups.append({'key': key, 'label': label,
                           'items': [item for item in data['items'] if item['group'] == key]})
        data['groups'] = groups
        data['closed'] = [item for item in data['items'] if item['group'] == 'closed'][-30:][::-1]
    from .views import _linkify_rendered_html, _render_markdown
    for item in [selected] if selected else data['items']:
        item['explanation_html'] = _linkify_rendered_html(_render_markdown(item['explanation']))
    return render(request, 'tjai_app/poke.html', {'selected': selected, 'data': data,
        'error': error, 'form': form, 'response_status': 409 if error else 200}, status=409 if error else 200)
