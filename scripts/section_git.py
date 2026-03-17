#!/usr/bin/env python3
"""Appends ## Git to the daily synopsis entry."""
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

UTC = timezone.utc
REPOS = [
    (Path('/home/admin/github/tjrepo'), 'https://github.com/wenaus/tjrepo', 'tjrepo'),
    (Path('/home/admin/github/swf-testbed'), 'https://github.com/BNLNPPS/swf-testbed', 'swf-testbed'),
    (Path('/home/admin/github/swf-monitor'), 'https://github.com/BNLNPPS/swf-monitor', 'swf-monitor'),
    (Path('/home/admin/github/swf-common-lib'), 'https://github.com/BNLNPPS/swf-common-lib', 'swf-common-lib'),
]

logger = logging.getLogger('section_git')


def _repo_commits(repo_dir, github_url, since_iso):
    """Return list of markdown lines for commits in one repo, or empty list."""
    try:
        result = subprocess.run(
            ['git', 'log', f'--since={since_iso}',
             '--format=%H%x00%s%x00%b%x01'],
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
        commits = _repo_commits(repo_dir, github_url, since_iso)
        if commits:
            all_lines.append(f'**{label}**')
            all_lines.extend(commits)
            all_lines.append('')

    return '\n'.join(all_lines).rstrip() if all_lines else None


if __name__ == '__main__':
    main_section('Git', build)
