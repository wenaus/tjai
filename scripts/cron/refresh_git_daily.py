#!/usr/bin/env python3
"""Regenerate data/git_daily/<date>.md files from local git state.

Refreshes today + yesterday (covers in-flight days and late-midnight commits)
and heals any empty files in the last 30 days (catches past days whose file
got written before that day's commits happened).

Runs via cron; the git_activity view just reads the files.
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
import bootstrap  # noqa: F401,E402 — Django setup

from django.conf import settings as django_settings  # noqa: E402
from tjai_app.services import get_timezone  # noqa: E402
from tjai_app.views import _GIT_REPOS  # noqa: E402

logger = logging.getLogger('refresh_git_daily')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

HEAL_LOOKBACK_DAYS = 30


def regen_date(target):
    """Regenerate git_daily/<target>.md. Returns (path, bytes_written)."""
    tz = get_timezone()
    since_dt = datetime(target.year, target.month, target.day, tzinfo=tz)
    until_dt = since_dt + timedelta(days=1)
    since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
    until_iso = until_dt.strftime('%Y-%m-%dT%H:%M:%S%z')

    all_lines = []
    for repo_path, github_url, label in _GIT_REPOS:
        if not os.path.isdir(repo_path):
            continue
        try:
            result = subprocess.run(
                ['git', '-c', 'safe.directory=*', 'log',
                 f'--since={since_iso}', f'--until={until_iso}',
                 '--format=%H%x00%s%x00%b%x01'],
                capture_output=True, timeout=10, cwd=repo_path,
                encoding='utf-8', errors='replace',
            )
        except Exception as e:
            logger.error("git log failed for %s: %s", label, e)
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue

        is_monorepo = label == 'tjrepo'
        commit_groups = {}
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
            md = [f'- [{subject}]({url})']
            if body:
                for bl in body.split('\n'):
                    bl = bl.strip()
                    if bl and not bl.startswith('Co-Authored-By:'):
                        bl = bl.lstrip('- ')
                        if len(bl) > 90:
                            bl = bl[:87] + '...'
                        md.append(f'  - {bl}')
                        break
            grp = label
            if is_monorepo:
                try:
                    dr = subprocess.run(
                        ['git', '-c', 'safe.directory=*', 'diff-tree',
                         '--no-commit-id', '--name-only', '-r', sha],
                        capture_output=True, timeout=5, cwd=repo_path,
                        encoding='utf-8', errors='replace',
                    )
                    counts = {}
                    for f in dr.stdout.strip().split('\n'):
                        d = f.split('/')[0] if '/' in f else '(root)'
                        counts[d] = counts.get(d, 0) + 1
                    if counts:
                        grp = max(counts, key=counts.get)
                except Exception:
                    pass
            commit_groups.setdefault(grp, []).extend(md)
        for grp, lines in commit_groups.items():
            all_lines.append(f'**{grp}**')
            all_lines.extend(lines)
            all_lines.append('')

    git_dir = Path(django_settings.BASE_DIR) / 'data' / 'git_daily'
    git_dir.mkdir(parents=True, exist_ok=True, mode=0o777)
    fpath = git_dir / f'{target.isoformat()}.md'
    content = ('\n'.join(all_lines).rstrip() + '\n') if all_lines else ''
    old_umask = os.umask(0)
    try:
        fd = os.open(str(fpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
    finally:
        os.umask(old_umask)
    return fpath, len(content)


def main():
    tz = get_timezone()
    today = datetime.now(tz=tz).date()

    targets = [today, today - timedelta(days=1)]

    # Heal: any empty file in last HEAL_LOOKBACK_DAYS. Legitimately-empty days
    # (no commits) stay empty on regen — cheap and idempotent. Falsely-empty
    # days (file written before the day's commits) get fixed.
    git_dir = Path(django_settings.BASE_DIR) / 'data' / 'git_daily'
    for offset in range(2, HEAL_LOOKBACK_DAYS + 1):
        d = today - timedelta(days=offset)
        f = git_dir / f'{d.isoformat()}.md'
        if f.exists() and f.stat().st_size == 0:
            targets.append(d)

    for d in targets:
        try:
            path, size = regen_date(d)
            logger.info("regen %s -> %d bytes", d.isoformat(), size)
        except Exception as e:
            logger.error("regen %s failed: %s", d.isoformat(), e)


if __name__ == '__main__':
    main()
