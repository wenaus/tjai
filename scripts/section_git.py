#!/usr/bin/env python3
"""Appends ## Git to the daily synopsis entry."""
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from django.conf import settings
from tjai_app.tjai_utils import get_app_tz
from synopsis_utils import main_section
GIT_DAILY_DIR = Path(settings.BASE_DIR) / 'data' / 'git_daily'
REPOS = [
    (Path('/home/admin/github/tjrepo'), 'https://github.com/wenaus/tjrepo', 'tjrepo'),
    (Path('/home/admin/github/tjdev'), 'https://github.com/wenaus/tjdev', 'tjdev'),
    (Path('/home/admin/github/swf-testbed'), 'https://github.com/BNLNPPS/swf-testbed', 'swf-testbed'),
    (Path('/home/admin/github/swf-monitor'), 'https://github.com/BNLNPPS/swf-monitor', 'swf-monitor'),
    (Path('/home/admin/github/swf-common-lib'), 'https://github.com/BNLNPPS/swf-common-lib', 'swf-common-lib'),
    (Path('/home/admin/github/swf-remote'), 'https://github.com/BNLNPPS/swf-remote', 'swf-remote'),
    (Path('/home/admin/github/BNLNPPS.github.io'), 'https://github.com/BNLNPPS/BNLNPPS.github.io', 'BNLNPPS.github.io'),
    (Path('/home/admin/github/lxr-mcp-server'), 'https://github.com/BNLNPPS/lxr-mcp-server', 'lxr-mcp-server'),
    (Path('/home/admin/github/corun-ai'), 'https://github.com/BNLNPPS/corun-ai', 'corun-ai'),
    (Path('/home/admin/github/rucio-eic-mcp-server'), 'https://github.com/BNLNPPS/rucio-eic-mcp-server', 'rucio-eic-mcp-server'),
]
# Monorepo: attribute commits to top-level subdirectory instead of repo name
MONOREPO_SUBDIRS = {
    'tjrepo': True,  # repos listed here get per-subdir attribution
}

logger = logging.getLogger('section_git')


def _repo_commits(repo_dir, github_url, since_iso, until_iso=None):
    """Return list of (label, markdown_lines) for commits in one repo.

    For monorepos, label is the top-level subdirectory. Otherwise repo name.
    Returns list of (label, [line, ...]) tuples.
    """
    try:
        cmd = ['git', 'log', '--all', f'--since={since_iso}',
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

    commits = []
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
        md_lines = [f'- [{subject}]({url})']

        # First substantive body line as a nested sub-item
        if body:
            for bl in body.split('\n'):
                bl = bl.strip()
                if bl and not bl.startswith('Co-Authored-By:'):
                    bl = bl.lstrip('- ')
                    if len(bl) > 90:
                        bl = bl[:87] + '...'
                    md_lines.append(f'  - {bl}')
                    break

        commits.append((sha, md_lines))

    return commits


def _commit_subdir(repo_dir, sha):
    """Determine the primary top-level subdirectory for a commit."""
    try:
        result = subprocess.run(
            ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', sha],
            capture_output=True, text=True, timeout=5, cwd=repo_dir,
        )
        counts = {}
        for f in result.stdout.strip().split('\n'):
            d = f.split('/')[0] if '/' in f else '(root)'
            counts[d] = counts.get(d, 0) + 1
        if not counts:
            return None
        # Return the subdirectory with the most changed files
        return max(counts, key=counts.get)
    except Exception:
        return None


def _group_commits_by_subdir(repo_dir, commits):
    """Group commits by top-level subdirectory. Returns {label: [md_lines]}."""
    from collections import OrderedDict
    groups = OrderedDict()
    for sha, md_lines in commits:
        subdir = _commit_subdir(repo_dir, sha) or 'other'
        groups.setdefault(subdir, []).extend(md_lines)
    return groups


def build(since_ts, target_date):
    """Return markdown body or None."""
    tz = get_app_tz()
    since_dt = datetime.fromtimestamp(since_ts, tz=tz)
    since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S%z')

    all_lines = []
    for repo_dir, github_url, label in REPOS:
        if not repo_dir.exists():
            continue
        try:
            subprocess.run(['git', 'fetch', '--all', '--prune'], capture_output=True,
                           timeout=30, cwd=repo_dir)
        except Exception as e:
            logger.warning("git fetch failed for %s: %s", label, e)
        commits = _repo_commits(repo_dir, github_url, since_iso)
        if commits:
            if label in MONOREPO_SUBDIRS:
                for subdir, md_lines in _group_commits_by_subdir(repo_dir, commits).items():
                    all_lines.append(f'**{subdir}**')
                    all_lines.extend(md_lines)
                    all_lines.append('')
            else:
                all_lines.append(f'**{label}**')
                for _, md_lines in commits:
                    all_lines.extend(md_lines)
                all_lines.append('')

    body = '\n'.join(all_lines).rstrip() if all_lines else None

    # Save daily file for the Git activity page
    if body and target_date:
        old_umask = os.umask(0)
        try:
            GIT_DAILY_DIR.mkdir(parents=True, exist_ok=True, mode=0o777)
            p = GIT_DAILY_DIR / f'{target_date.isoformat()}.md'
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
            os.write(fd, (body + '\n').encode())
            os.close(fd)
        finally:
            os.umask(old_umask)

    return body


def backfill(days=60):
    """Generate git daily files for the past N days."""
    from datetime import timedelta
    tz = get_app_tz()
    today = datetime.now(tz=tz).date()
    for i in range(days):
        target = today - timedelta(days=i)
        since_dt = datetime(target.year, target.month, target.day, tzinfo=tz)
        until_dt = since_dt + timedelta(days=1)
        since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
        until_iso = until_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
        all_lines = []
        for repo_dir, github_url, label in REPOS:
            if not repo_dir.exists():
                continue
            commits = _repo_commits(repo_dir, github_url, since_iso, until_iso)
            if commits:
                if label in MONOREPO_SUBDIRS:
                    for subdir, md_lines in _group_commits_by_subdir(repo_dir, commits).items():
                        all_lines.append(f'**{subdir}**')
                        all_lines.extend(md_lines)
                        all_lines.append('')
                else:
                    all_lines.append(f'**{label}**')
                    for _, md_lines in commits:
                        all_lines.extend(md_lines)
                    all_lines.append('')
        content = ('\n'.join(all_lines).rstrip() + '\n') if all_lines else ''
        old_umask = os.umask(0)
        try:
            GIT_DAILY_DIR.mkdir(parents=True, exist_ok=True, mode=0o777)
            p = GIT_DAILY_DIR / f'{target.isoformat()}.md'
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
            os.write(fd, content.encode())
            os.close(fd)
        finally:
            os.umask(old_umask)
        if all_lines:
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
