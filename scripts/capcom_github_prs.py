#!/usr/bin/env python3
"""GitHub pull-request follow-up collector for Capcom (docs/capcom.md).

Runs as the github-pr-followups action's mechanical_script (wrangler-owned,
hourly). Discovers every pull request authored by the token's GitHub account
that changed since the cursor — one GitHub search, no repository list — and
emits a Capcom notice for each human event on those PRs since the cursor:
issue comments, review comments, submitted reviews, and close, merge, and
reopen. Events by the account itself and by bots are skipped. Every event is
its own notice row; the dedup_key names the event (repo, PR number, verb,
time) so a re-run never duplicates one.

Cursor: the capcom_github_prs_cursor sysconfig row holds the newest event
time processed (UTC, ISO). The first run seeds it LOOKBACK_DAYS back.
Token: GITHUB_PERSONAL_ACCESS_TOKEN from the user's ~/.env (never the
deployed project .env).

Usage:
    capcom_github_prs.py            run (emit notices, advance the cursor)
    capcom_github_prs.py --dry-run  print what would be emitted; write nothing
"""
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import bootstrap  # noqa: F401 - Django setup
import requests

from tjai_app import capcom
from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import SysConfig

SOURCE = 'github-pr-followups'
SOURCE_NOTE = 'Human follow-ups on my GitHub pull requests (hourly discovery)'
CURSOR_KEY = 'capcom_github_prs_cursor'
LOOKBACK_DAYS = 14
API = 'https://api.github.com'
SEARCH_PAGES = 5
TIMEOUT = 30
SNIPPET = 90        # title excerpt length
DETAIL = 1200       # detail body length

logger = logging.getLogger('capcom-github')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='capcom-github')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(ts):
    return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _session(token):
    s = requests.Session()
    s.headers.update({
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'tjai-capcom-github-prs',
    })
    return s


def _get(session, url, params=None):
    try:
        r = session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f'GitHub request failed: {url}: {e}') from e
    if r.status_code != 200:
        raise RuntimeError(f'GitHub returned HTTP {r.status_code} for {url}: {r.text[:200]}')
    try:
        return r.json()
    except ValueError as e:
        raise RuntimeError(f'GitHub returned invalid JSON for {url}') from e


def _is_bot(user):
    login = (user or {}).get('login') or ''
    return (user or {}).get('type') == 'Bot' or login.endswith('[bot]') or login == 'Copilot'


def _first_line(text, limit):
    line = (text or '').replace('\r', '').strip().split('\n', 1)[0].strip()
    return line if len(line) <= limit else line[:limit - 1].rstrip() + '…'


def _token():
    """GITHUB_PERSONAL_ACCESS_TOKEN from ~/.env, read directly: the file is a
    shell environment file, not dotenv syntax, and only this one value is needed."""
    path = os.path.expanduser('~/.env')
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('export '):
                    line = line[7:]
                if line.startswith('GITHUB_PERSONAL_ACCESS_TOKEN='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError as e:
        raise RuntimeError(f'cannot read {path}: {e}') from e
    return ''


def _read_cursor():
    row = SysConfig.objects.filter(key=CURSOR_KEY).first()
    value = (row.value or '').strip() if row else ''
    if value:
        return _parse(value)
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def _write_cursor(dt):
    SysConfig.objects.update_or_create(
        key=CURSOR_KEY, defaults={'value': _iso(dt), 'timestamp_modified': time.time()})


def discover_prs(session, login, cursor):
    """Every PR authored by `login` updated at or after the cursor, anywhere."""
    items, query = [], f'is:pr author:{login} updated:>={_iso(cursor)}'
    for page in range(1, SEARCH_PAGES + 1):
        data = _get(session, f'{API}/search/issues',
                    {'q': query, 'sort': 'updated', 'order': 'asc',
                     'per_page': 100, 'page': page})
        batch = data.get('items') or []
        items.extend(batch)
        if len(batch) < 100:
            break
    else:
        logger.warning('github prs: search page limit reached, remainder next run')
    return items


def pr_events(session, repo, number, login, cursor):
    """Human events on one PR strictly after the cursor, oldest first."""
    since = {'since': _iso(cursor), 'per_page': 100}
    events = []

    for c in _get(session, f'{API}/repos/{repo}/issues/{number}/comments', since):
        events.append(('commented', c['created_at'], c.get('user'), c['html_url'], c.get('body')))

    for c in _get(session, f'{API}/repos/{repo}/pulls/{number}/comments', since):
        events.append((f"commented on {c.get('path')}", c['created_at'], c.get('user'),
                       c['html_url'], c.get('body')))

    for rv in _get(session, f'{API}/repos/{repo}/pulls/{number}/reviews', {'per_page': 100}):
        state, body = rv.get('state') or '', rv.get('body') or ''
        if not rv.get('submitted_at'):
            continue
        if state == 'COMMENTED' and not body.strip():
            continue                      # empty container for inline comments, already covered
        verb = {'APPROVED': 'approved', 'CHANGES_REQUESTED': 'requested changes',
                'DISMISSED': 'dismissed a review'}.get(state, 'reviewed')
        events.append((verb, rv['submitted_at'], rv.get('user'), rv['html_url'], body))

    state_events = [e for e in _get(session, f'{API}/repos/{repo}/issues/{number}/events',
                                    {'per_page': 100})
                    if e.get('event') in ('closed', 'merged', 'reopened')]
    merged_at = [_parse(e['created_at']) for e in state_events if e['event'] == 'merged']
    for e in state_events:
        if e['event'] == 'closed' and any(
                abs((_parse(e['created_at']) - m).total_seconds()) <= 120 for m in merged_at):
            continue                      # a merge emits merged + closed seconds apart; keep merged
        verb = {'closed': 'closed', 'merged': 'merged', 'reopened': 'reopened'}[e['event']]
        events.append((verb, e['created_at'], e.get('actor'), '', ''))

    out = []
    for verb, ts, user, url, body in events:
        if _parse(ts) <= cursor:
            continue
        actor = (user or {}).get('login') or 'someone'
        if actor == login or _is_bot(user):
            continue
        out.append({'verb': verb, 'at': _parse(ts), 'actor': actor, 'url': url, 'body': body or ''})
    out.sort(key=lambda e: e['at'])
    return out


def run(dry_run=False):
    token = os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN', '').strip() or _token()
    if not token:
        raise RuntimeError('GITHUB_PERSONAL_ACCESS_TOKEN is not set in ~/.env')
    session = _session(token)
    login = _get(session, f'{API}/user').get('login')
    if not login:
        raise RuntimeError('GitHub /user returned no login for the token')

    cursor = _read_cursor()
    newest = cursor
    if not dry_run:
        capcom.ensure_source(SOURCE, kind='feed', mode='listen', note=SOURCE_NOTE)

    prs = discover_prs(session, login, cursor)
    emitted = 0
    for pr in prs:
        repo = pr['repository_url'].split('/repos/', 1)[1]
        number = pr['number']
        pr_url = pr['html_url']
        for ev in pr_events(session, repo, number, login, cursor):
            snippet = _first_line(ev['body'], SNIPPET)
            title = f"{repo}#{number} · {ev['actor']} {ev['verb']}"
            if snippet:
                title += f': {snippet}'
            severity = 'warning' if ev['verb'] in ('requested changes', 'closed') else 'info'
            detail = f"{pr['title']}\n{pr_url}\n\n{ev['at'].strftime('%Y-%m-%d %H:%M UTC')}  {ev['actor']} {ev['verb']}"
            if ev['body'].strip():
                detail += '\n\n' + ev['body'].replace('\r', '').strip()[:DETAIL]
            if dry_run:
                print(f"[{severity}] {title}\n    {ev['url'] or pr_url}")
            else:
                capcom.emit_notice(source=SOURCE, title=title, severity=severity,
                                   url=ev['url'] or pr_url,
                                   dedup_key=f"github-pr:{repo}#{number}:{ev['verb']}:{_iso(ev['at'])}",
                                   data={'detail': detail, 'pr_url': pr_url})
            emitted += 1
            if ev['at'] > newest:
                newest = ev['at']

    if dry_run:
        print(f'dry run: {len(prs)} PR(s) updated since {_iso(cursor)}, '
              f'{emitted} event(s) would be emitted; cursor would move to {_iso(newest)}')
        return
    if newest > cursor:
        _write_cursor(newest)
    if emitted:
        logger.info('github prs: %d event(s) on %d PR(s) since %s', emitted, len(prs), _iso(cursor))


if __name__ == '__main__':
    try:
        run(dry_run='--dry-run' in sys.argv[1:])
    except Exception as e:  # surface every failure: AppLog ERROR + nonzero exit
        logger.error('github prs collector failed: %s', e)
        sys.exit(1)
