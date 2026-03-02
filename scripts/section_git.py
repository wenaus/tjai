#!/usr/bin/env python3
"""Appends ## Git to the daily synopsis entry."""
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

UTC = timezone.utc
REPO_DIR = Path('/home/admin/github/tjrepo')
GITHUB_URL = 'https://github.com/wenaus/tjrepo'

logger = logging.getLogger('section_git')


def build(since_ts, target_date):
    """Return markdown body or None."""
    since_dt = datetime.fromtimestamp(since_ts, tz=UTC)
    since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S')

    try:
        result = subprocess.run(
            ['git', 'log', f'--since={since_iso}',
             '--format=%H%x00%s%x00%b%x01'],
            capture_output=True, text=True, timeout=10, cwd=REPO_DIR,
        )
    except Exception as e:
        logger.error("Git log failed: %s", e)
        return None

    if not result.stdout.strip():
        return None

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

        url = f'{GITHUB_URL}/commit/{sha}'
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

    return '\n'.join(lines) if lines else None


if __name__ == '__main__':
    main_section('Git', build)
