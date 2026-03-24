#!/usr/bin/env python3
"""Appends ## Git to the daily synopsis entry."""
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from django.conf import settings
from synopsis_utils import main_section

UTC = timezone.utc
GIT_DAILY_DIR = Path(settings.BASE_DIR) / 'data' / 'git_daily'
REPOS = [
    (Path('/home/admin/github/tjrepo'), 'https://github.com/wenaus/tjrepo', 'tjrepo'),
    (Path('/home/admin/github/swf-testbed'), 'https://github.com/BNLNPPS/swf-testbed', 'swf-testbed'),
    (Path('/home/admin/github/swf-monitor'), 'https://github.com/BNLNPPS/swf-monitor', 'swf-monitor'),
    (Path('/home/admin/github/swf-common-lib'), 'https://github.com/BNLNPPS/swf-common-lib', 'swf-common-lib'),
    (Path('/home/admin/github/swf-remote'), 'https://github.com/BNLNPPS/swf-remote', 'swf-remote'),
    (Path('/home/admin/github/BNLNPPS.github.io'), 'https://github.com/BNLNPPS/BNLNPPS.github.io', 'BNLNPPS.github.io'),
]

logger = logging.getLogger('section_git')


def _repo_commits(repo_dir, github_url, since_iso, until_iso=None):
    """Return list of markdown lines for commits in one repo, or empty list."""
    try:
        cmd = ['git', 'log', f'--since={since_iso}',
               '--format=%H%x00%s%x00%b%x01']
        if until_iso:
            cmd.insert(3, f'--until={until_iso}')
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
    except Exception as e:
        logger.error("Git log failed for %s: %s", repo_dir, e)
        return []

    if not result.stdout.strip():
        return []

    lines = []
    for chunk in result.stdout.split('\x01'):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split('\x00', 2)
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ''

        url = f'{github_url}/commit/{sha}'
        lines.append(f'- [{subject}]({url})')

        # First substantive body line as a nested sub-item
        if body:
            for bl in body.split('\n'):
                bl = bl.strip()
                if bl and not bl.startswith('Co-Authored-By:'):
                    if len(bl) > 90:
                        bl = bl[:87] + '...'
                    lines.append(f'  - {bl}')
                    break

    return lines


def build(since_ts, target_date):
    """Return markdown body or None."""
    since_dt = datetime.fromtimestamp(since_ts, tz=UTC)
    since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S')

    all_lines = []
    for repo_dir, github_url, label in REPOS:
        if not repo_dir.exists():
            continue
        try:
            subprocess.run(['git', 'pull', '--ff-only'], capture_output=True,
                           timeout=30, cwd=repo_dir)
        except Exception as e:
            logger.warning("git pull failed for %s: %s", label, e)
        commits = _repo_commits(repo_dir, github_url, since_iso)
        if commits:
            all_lines.append(f'**{label}**')
            all_lines.extend(commits)
            all_lines.append('')

    body = '\n'.join(all_lines).rstrip() if all_lines else None

    # Save daily file for the Git activity page
    if body and target_date:
        GIT_DAILY_DIR.mkdir(parents=True, exist_ok=True)
        (GIT_DAILY_DIR / f'{target_date.isoformat()}.md').write_text(body + '\n')

    return body


def backfill(days=60):
    """Generate git daily files for the past N days."""
    from datetime import timedelta
    today = datetime.now(tz=UTC).date()
    for i in range(days):
        target = today - timedelta(days=i)
        since_dt = datetime(target.year, target.month, target.day, tzinfo=UTC)
        until_dt = since_dt + timedelta(days=1)
        since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S')
        until_iso = until_dt.strftime('%Y-%m-%dT%H:%M:%S')
        all_lines = []
        for repo_dir, github_url, label in REPOS:
            if not repo_dir.exists():
                continue
            commits = _repo_commits(repo_dir, github_url, since_iso, until_iso)
            if commits:
                all_lines.append(f'**{label}**')
                all_lines.extend(commits)
                all_lines.append('')
        if all_lines:
            body = '\n'.join(all_lines).rstrip()
            GIT_DAILY_DIR.mkdir(parents=True, exist_ok=True)
            (GIT_DAILY_DIR / f'{target.isoformat()}.md').write_text(body + '\n')
            print(f'{target}: {len(all_lines)} lines')
        else:
            print(f'{target}: no commits')


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        backfill(days)
    else:
        main_section('Git', build)
