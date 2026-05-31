#!/usr/bin/env python3
"""Generate dev activity report from upstream repos (6-week lookback).

Uses GitHub API (gh CLI) for PR events and git log for direct commits.
Three event types: PR opened, PR merged, direct commit.

Writes /var/www/tjai/data/dev_daily/YYYY-MM-DD.md

Usage:
    python section_dev.py              # generate today's report (6-week window)
    python section_dev.py backfill 30  # backfill last 30 days
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# tjai operates in Eastern time exclusively (SysConfig timezone = America/New_York).
# Standalone by design: no Django bootstrap, so cron runs without the app's env vars.
APP_TZ = ZoneInfo('America/New_York')

DEV_DAILY_DIR = Path('/var/www/tjai/data/dev_daily')

# (local_path, github_owner/repo, display_name)
REPOS = sorted([
    ('/home/admin/github/harvester', 'HSF/harvester', 'harvester'),
    ('/home/admin/github/iDDS', 'HSF/iDDS', 'iDDS'),
    ('/home/admin/github/panda-bigmon-core', 'PanDAWMS/panda-bigmon-core', 'panda-bigmon-core'),
    ('/home/admin/github/panda-client', 'PanDAWMS/panda-client', 'panda-client'),
    ('/home/admin/github/panda-compose', 'PanDAWMS/panda-compose', 'panda-compose'),
    ('/home/admin/github/panda-server', 'PanDAWMS/panda-server', 'panda-server'),
    ('/home/admin/github/pilot3', 'PanDAWMS/pilot3', 'pilot3'),
    ('/home/admin/github/pilot-wrapper', 'PanDAWMS/pilot-wrapper', 'pilot-wrapper'),
], key=lambda r: r[2].lower())


def _collapse_paths(files):
    """Collapse files in same directory: full path on first, basename after."""
    files = sorted(files)
    parts = []
    last_dir = None
    for f in files:
        d = '/'.join(f.split('/')[:-1])
        basename = f.split('/')[-1]
        if d and d == last_dir:
            parts.append(f'{basename}')
        else:
            parts.append(f'{f}')
        last_dir = d
    return parts


def _changed_files(repo_dir, sha):
    """Return list of changed file paths for a commit."""
    try:
        result = subprocess.run(
            ['git', '-c', 'safe.directory=*', 'diff-tree',
             '--no-commit-id', '--name-only', '-r', sha],
            capture_output=True, text=True, timeout=5, cwd=repo_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    except Exception:
        pass
    return []


def _pr_commit_count(gh_repo, pr_num):
    """Get commit count for a single PR via gh API."""
    try:
        result = subprocess.run(
            ['gh', 'pr', 'view', str(pr_num), '--repo', gh_repo, '--json', 'commits', '--jq', '.commits | length'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def _gh_prs(gh_repo, since_date, until_date):
    """Get PRs from GitHub API via gh CLI. Returns list of event dicts."""
    # Get recently updated PRs (merged or open) — gh searches by update time
    cmd = [
        'gh', 'pr', 'list', '--repo', gh_repo,
        '--state', 'all', '--limit', '100',
        '--json', 'number,title,body,author,createdAt,mergedAt,state,url,files',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        print(f"ERROR: gh pr list failed for {gh_repo}: {e}", file=sys.stderr)
        return []

    if result.returncode != 0:
        print(f"ERROR: gh pr list {gh_repo} rc={result.returncode}: {result.stderr.strip()}", file=sys.stderr)
        return []

    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"ERROR: gh JSON parse for {gh_repo}: {e}", file=sys.stderr)
        return []

    events = []
    gh_url = f'https://github.com/{gh_repo}'

    for pr in prs:
        pr_num = pr['number']
        title = pr['title']
        body = (pr.get('body') or '').strip()
        author = pr.get('author', {}).get('login', '?')
        pr_url = pr.get('url', f'{gh_url}/pull/{pr_num}')

        # Files changed
        pr_files = [f.get('path', '') for f in (pr.get('files') or []) if f.get('path')]

        # Check if this PR falls in the window
        created = pr.get('createdAt', '')[:10]
        merged = (pr.get('mergedAt') or '')[:10]
        in_window = (since_date <= created <= until_date) or (merged and since_date <= merged <= until_date)
        if not in_window:
            continue

        # Get commit count for this PR (individual API call, only for PRs in window)
        commit_count = _pr_commit_count(gh_repo, pr_num)
        cc = f' ({commit_count})' if commit_count else ''

        # PR opened event
        if since_date <= created <= until_date:
            dt = datetime.strptime(created, '%Y-%m-%d')
            events.append({
                'sort_key': created,
                'date': dt.strftime('%b %-d'),
                'label': f'PR opened{cc}',
                'subject': f'#{pr_num}: {title}',
                'url': pr_url,
                'author': author,
                'body': body,
                'files': pr_files,
            })

        # PR merged event
        if merged and since_date <= merged <= until_date:
            dt = datetime.strptime(merged, '%Y-%m-%d')
            events.append({
                'sort_key': merged,
                'date': dt.strftime('%b %-d'),
                'label': f'PR merged{cc}',
                'subject': f'#{pr_num}: {title}',
                'url': pr_url,
                'author': author,
                'body': body,
                'files': pr_files,
            })

    return events


def _direct_commits(repo_dir, gh_repo, since_iso, until_iso):
    """Get direct-to-main commits (not PR merges) via git log."""
    cmd = ['git', '-c', 'safe.directory=*', 'log', '--first-parent', '--no-merges',
           f'--since={since_iso}', f'--until={until_iso}',
           '--format=%H%x00%aI%x00%an%x00%s%x01']
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
    except Exception as e:
        print(f"ERROR: git log failed for {repo_dir}: {e}", file=sys.stderr)
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    gh_url = f'https://github.com/{gh_repo}'
    events = []
    for chunk in result.stdout.split('\x01'):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split('\x00', 3)
        if len(parts) < 4:
            continue
        sha = parts[0].strip()
        date_iso = parts[1].strip()[:10]
        author = parts[2].strip()
        subject = parts[3].strip()

        dt = datetime.strptime(date_iso, '%Y-%m-%d')
        files = _changed_files(repo_dir, sha)

        events.append({
            'sort_key': date_iso,
            'date': dt.strftime('%b %-d'),
            'label': 'commit',
            'subject': subject,
            'url': f'{gh_url}/commit/{sha}',
            'author': author,
            'files': files,
        })

    return events


def generate_day(target_date, tz, lookback_days=42):
    """Generate dev report covering the last lookback_days. Returns total event count."""
    until_dt = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz) + timedelta(days=1)
    since_dt = until_dt - timedelta(days=lookback_days)
    since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
    until_iso = until_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
    since_date = since_dt.strftime('%Y-%m-%d')
    until_date = until_dt.strftime('%Y-%m-%d')

    all_lines = []
    total = 0

    for repo_dir, gh_repo, label in REPOS:
        gh_url = f'https://github.com/{gh_repo}'
        all_lines.append(f'<h2><a href="{gh_url}">{label}</a></h2>')

        if not os.path.isdir(repo_dir):
            all_lines.append('<p class="empty">repo not cloned</p>')
            continue

        # Gather both PR events and direct commits
        events = _gh_prs(gh_repo, since_date, until_date)
        events += _direct_commits(repo_dir, gh_repo, since_iso, until_iso)

        if not events:
            all_lines.append(f'<p class="empty">No activity in the last {lookback_days} days.</p>')
            continue

        # Sort reverse chronological
        events.sort(key=lambda e: e['sort_key'], reverse=True)
        total += len(events)

        for e in events:
            body = e.get('body', '')
            files = e.get('files', [])

            # Event line — always visible
            all_lines.append(f'<div class="event">')
            all_lines.append(f'<div class="event-line"><b>{e["date"]}</b> {e["label"]} — <a href="{e["url"]}">{e["subject"]}</a> — {e["author"]}</div>')

            # Files — always visible
            if files:
                collapsed = _collapse_paths(files)
                file_summary = ', '.join(collapsed)
                all_lines.append(f'<div class="files">{file_summary}</div>')

            # Body — show directly if <10 lines, collapsible otherwise
            if body:
                body_lines = [l for l in body.split('\n') if l.strip()]
                if len(body_lines) < 10:
                    all_lines.append(f'<div class="body-oneline">{body}</div>')
                else:
                    first_line = body_lines[0].strip()
                    rest = '\n'.join(body_lines[1:])
                    display_rest = rest[:500] + ('...' if len(rest) > 500 else '')
                    all_lines.append(f'<details><summary>{first_line}</summary>')
                    all_lines.append(f'<p class="body-text">{display_rest}</p>')
                    all_lines.append(f'</details>')

            all_lines.append(f'</div>')

        all_lines.append('')

    content = '\n'.join(all_lines).rstrip() + '\n'

    old_umask = os.umask(0)
    try:
        DEV_DAILY_DIR.mkdir(parents=True, exist_ok=True, mode=0o777)
        fpath = DEV_DAILY_DIR / f'{target_date.isoformat()}.md'
        fd = os.open(str(fpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
    finally:
        os.umask(old_umask)

    return total


def main():
    tz = APP_TZ
    today = datetime.now(tz=tz).date()

    if len(sys.argv) > 1 and sys.argv[1] == 'backfill':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        for i in range(days):
            target = today - timedelta(days=i)
            count = generate_day(target, tz)
            print(f'{target}: {count} events')
    else:
        count = generate_day(today, tz)
        print(f'{today}: {count} events')


if __name__ == '__main__':
    main()
