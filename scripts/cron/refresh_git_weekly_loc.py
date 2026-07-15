#!/usr/bin/env python3
"""Regenerate data/git_weekly_loc.json — weekly lines-added by project.

Feeds the stacked-bar chart below the git grid on the git activity page.
Counts lines added (git numstat) in commits authored by AUTHOR across all
git repositories under GITHUB_ROOT, bucketed by ISO week (Monday start,
tjai timezone) from WEEK_ZERO onward, attributed to a project by repo —
and for tjrepo by top-level directory.

Runs via cron; the page's data endpoint just reads the JSON file.
"""
import json
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

logger = logging.getLogger('refresh_git_weekly_loc')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

AUTHOR = 'wenaus@gmail.com'
GITHUB_ROOT = Path('/home/admin/github')
WEEK_ZERO = datetime(2025, 6, 2).date()  # Monday on/after 2025-06-01

# Display order == stacking order (bottom-up) == legend order. The order is
# color-validated (adjacent-pair CVD checks) — change it only with the
# template palette revalidated to match.
PROJECTS = ['kozykorner', 'tjai', 'corun-ai', 'swf', 'swf-monitor',
            'swf-epicprod', 'swf-testbed', 'etaverse', 'blender',
            'SL/OS', 'primus', 'pax-eden', 'other']

# Repo name -> project. Unlisted repos fall to 'other'; tjrepo is
# attributed per file by top-level directory instead. The swf family is
# factored into shades of one hue: monitor, epicprod, and testbed are
# called out; 'swf' is the family residual (remote, docs, common-lib).
REPO_PROJECT = {
    'swf-monitor': 'swf-monitor', 'swf-epicprod': 'swf-epicprod',
    'swf-testbed': 'swf-testbed',
    'swf-common-lib': 'swf', 'swf-remote': 'swf', 'epic-wfms-docs': 'swf',
    'corun-ai': 'corun-ai', 'corun-mcp-server': 'corun-ai',
    'wrangle-ai': 'corun-ai',
}

# tjrepo top-level directory -> project ('' catches root-level files).
# blender is its own family: the 3D content pipeline crosses etaverse,
# SL/OS, and primus, so it carries its own color rather than a guess.
TJREPO_DIR_PROJECT = {
    'tjai': 'tjai', 'etaverse': 'etaverse', 'kozykorner': 'kozykorner',
    'pax-eden': 'pax-eden', 'primus': 'primus', 'primus-blender': 'primus',
    'primus-sl': 'primus', 'primus-wright': 'primus',
    'blender': 'blender',
    'sl': 'SL/OS', 'lsl': 'SL/OS', 'lslp': 'SL/OS', 'wright': 'SL/OS',
}

# Path fragments excluded from line counts (generated/vendored files).
EXCLUDE_PARTS = ('node_modules/', '.venv/', 'venv/', 'staticfiles/',
                 'dist/', 'build/', '__pycache__/')
EXCLUDE_SUFFIXES = ('.min.js', '.min.css', 'package-lock.json', '.lock')

# tjrepo archive imports of pre-period work — not part of the year's coding.
EXCLUDE_TJREPO_TOP = {'etaverse-2014', 'tjai-archive', 'tjweb-old',
                      'tjweb-2013', 'sl2007'}

# A single commit adding more than this is a bulk import (e.g. the 296k-line
# recipe migration), dropped whole. Stated on the chart.
BULK_COMMIT_LINES = 100_000


def _excluded(path):
    if any(part in path for part in EXCLUDE_PARTS):
        return True
    return path.endswith(EXCLUDE_SUFFIXES)


def _project_for(repo_name, path):
    if repo_name == 'tjrepo':
        top = path.split('/', 1)[0] if '/' in path else ''
        if top in EXCLUDE_TJREPO_TOP:
            return None
        return TJREPO_DIR_PROJECT.get(top, 'other')
    return REPO_PROJECT.get(repo_name, 'other')


def _week_index(date_obj):
    """Monday-start week index from WEEK_ZERO; None if before it."""
    days = (date_obj - WEEK_ZERO).days
    if days < 0:
        return None
    return days // 7


def collect():
    tz = get_timezone()
    today = datetime.now(tz=tz).date()
    n_weeks = _week_index(today) + 1
    counts = {p: [0] * n_weeks for p in PROJECTS}

    repos = [d for d in sorted(GITHUB_ROOT.iterdir())
             if d.is_dir() and (d / '.git').exists()]
    for repo in repos:
        try:
            # -M enables rename detection so pure moves count ~zero lines
            # (with --no-renames, an in-repo rename booked every moved line
            # as new — a 2025-08 directory rename inflated primus by 21k).
            result = subprocess.run(
                ['git', '-c', 'safe.directory=*', 'log',
                 f'--author={AUTHOR}', f'--since={WEEK_ZERO}T00:00:00',
                 '--numstat', '-M', '--format=@%ad',
                 '--date=format-local:%Y-%m-%d'],
                capture_output=True, timeout=120, cwd=repo,
                encoding='utf-8', errors='replace',
                env={**os.environ, 'TZ': str(tz)},
            )
        except Exception as e:
            logger.error("%s: git log failed: %s", repo.name, e)
            continue
        if result.returncode != 0:
            logger.error("%s: git log rc=%s: %s", repo.name,
                         result.returncode, result.stderr.strip()[:200])
            continue

        week = None
        pending = []  # (project, adds) for the commit being read

        def flush():
            if week is not None and week < n_weeks:
                if sum(a for _, a in pending) <= BULK_COMMIT_LINES:
                    for proj, adds in pending:
                        counts[proj][week] += adds
            pending.clear()

        for line in result.stdout.splitlines():
            if line.startswith('@'):
                flush()
                try:
                    week = _week_index(
                        datetime.strptime(line[1:], '%Y-%m-%d').date())
                except ValueError:
                    week = None
                continue
            if not line or week is None:
                continue
            parts = line.split('\t')
            if len(parts) != 3 or parts[0] == '-':
                continue
            path = parts[2].strip('"')  # git C-quotes special-char paths
            if '=>' in path:
                # Rename numstat path: 'a/{old => new}/b' or 'old => new'.
                # Attribute the (usually few) changed lines to the new path.
                if '{' in path:
                    pre, rest = path.split('{', 1)
                    mid, post = rest.split('}', 1)
                    path = (pre + mid.split('=>')[1].strip() + post).replace('//', '/')
                else:
                    path = path.split('=>')[1].strip()
            if _excluded(path):
                continue
            proj = _project_for(repo.name, path)
            if proj is not None:
                pending.append((proj, int(parts[0])))
        flush()

    weeks = [(WEEK_ZERO + timedelta(weeks=i)).isoformat()
             for i in range(n_weeks)]
    return {
        'generated_at': datetime.now(tz=tz).isoformat(),
        'author': AUTHOR,
        'metric': 'lines added',
        'bulk_commit_lines': BULK_COMMIT_LINES,
        'week_start': weeks,
        'projects': PROJECTS,
        'series': counts,
    }


def main():
    data = collect()
    out = Path(django_settings.BASE_DIR) / 'data' / 'git_weekly_loc.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding='utf-8')
    total = sum(sum(v) for v in data['series'].values())
    logger.info("wrote %s: %d weeks, %d lines total",
                out, len(data['week_start']), total)


if __name__ == '__main__':
    main()
