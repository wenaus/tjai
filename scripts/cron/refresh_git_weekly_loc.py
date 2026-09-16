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
from tjai_app.views import GIT_AUTHOR as AUTHOR  # noqa: E402  (one definition)
from tjai_app.views import git_history_exclusions  # noqa: E402

logger = logging.getLogger('refresh_git_weekly_loc')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

GITHUB_ROOT = Path('/home/admin/github')
WEEK_ZERO = datetime(2025, 6, 2).date()  # Monday on/after 2025-06-01

# Display order == stacking order (bottom-up) == legend order. The order is
# color-validated (adjacent-pair CVD checks) — change it only with the
# template palette revalidated to match.
PROJECTS = ['tjai', 'AI', 'swf', 'swf-monitor',
            'swf-epicprod', 'swf-testbed', 'primus', 'etaverse',
            'blender', 'pax-eden', 'dev', 'other']

# Repo name -> project. Unlisted repos fall to 'other'; tjrepo is
# attributed per file by top-level directory instead. The swf family is
# factored into shades of one hue: monitor, epicprod, and testbed are
# called out; 'swf' is the family residual (remote, docs, common-lib,
# site-canary, rucio-eic). AI: AI infrastructure serving any project —
# corun-ai, wrangle-ai, snapper-ai (generic in service of swf, not
# swf-bound), and the MCP servers. tjai stays its own project.
REPO_PROJECT = {
    'tjai': 'tjai', 'tjlinks': 'tjai',
    'swf-monitor': 'swf-monitor', 'swf-epicprod': 'swf-epicprod',
    'swf-testbed': 'swf-testbed',
    'swf-common-lib': 'swf', 'swf-remote': 'swf', 'epic-wfms-docs': 'swf',
    'site-canary': 'swf', 'distcomp-services': 'swf',
    'corun-ai': 'AI', 'corun-mcp-server': 'AI',
    'wrangle-ai': 'AI', 'snapper-ai': 'AI', 'teamcomms-ai': 'AI',
    'lxr-mcp-server': 'AI', 'xrootd-mcp-server': 'AI',
    'rucio-eic-mcp-server': 'swf',
    # dev: development support — web/doc sites, tooling, machine and
    # workspace configuration
    'epic-web-demo': 'dev', 'BNLNPPS.github.io': 'dev',
    'tjdev': 'dev',
}

# tjrepo top-level directory -> project ('' catches root-level files).
# blender carries its own color: the 3D content pipeline serves both
# etaverse and primus. SL/OS scripting dirs count as primus; kozykorner
# and the infra/meta dirs count as dev.
TJREPO_DIR_PROJECT = {
    'tjai': 'tjai', 'tj-getlink': 'tjai',
    'etaverse': 'etaverse', 'kozykorner': 'dev',
    'pax-eden': 'pax-eden', 'primus': 'primus', 'primus-blender': 'primus',
    'primus-sl': 'primus', 'primus-wright': 'primus',
    'blender': 'blender',
    'sl': 'primus', 'lsl': 'primus', 'lslp': 'primus', 'wright': 'primus',
    'computers': 'dev', 'torre-code': 'dev', 'ops': 'dev', 'docs': 'dev',
    'talks': 'dev', '.claude': 'dev', '.devcontainer': 'dev',
    '.vscode': 'dev', '': 'dev',
}

# Path fragments excluded from line counts (generated/vendored files,
# and script-generated data files — data is not LOC). The -old/-broken/
# SUPERSEDED fragments drop snapshot copies of own code kept during
# refactors, which would otherwise double-count.
EXCLUDE_PARTS = ('node_modules/', '.venv/', 'venv/', 'staticfiles/',
                 'dist/', 'build/', '__pycache__/',
                 'cleanup_manifests/', 'cleanup_logs/',
                 'texture_catalog.txt', 'cove_house_reference',
                 '-old.', '_old.', '-broken.', '_broken.', 'SUPERSEDED')
# .dae/.bvh: tool-generated 3D geometry and motion-capture data.
# .dbml: auto-generated database schema diagrams.
EXCLUDE_SUFFIXES = ('.min.js', '.min.css', 'package-lock.json', '.lock',
                    '.dae', '.bvh', '.dbml')

# tjrepo ignore list: top-level dirs whose lines never count — archive
# imports of pre-period work, and staging dirs for material that is not
# this author's work product (e.g. transfers: patch handoffs, deleted
# 2026-08 but present in history).
EXCLUDE_TJREPO_TOP = {'etaverse-2014', 'tjai-archive', 'tjweb-old',
                      'tjweb-2013', 'sl2007', 'transfers'}

# A single commit adding more than this is a bulk import (e.g. the 296k-line
# recipe migration), dropped whole. Stated on the chart.
BULK_COMMIT_LINES = 100_000

# Cross-repo moves: git rename detection (-M) is per-repo, so code moved
# between repositories books as new lines. Enumerated manually and dropped.
CROSS_REPO_MOVE_COMMITS = {
    # pcs application + docs moved swf-monitor -> swf-epicprod (25k lines)
    'affc90be498c4f7c8aa2da96c52f24f14faf6883',
}


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

    # A worktree's .git is a file, not a directory; a worktree of a
    # followed repo would book its history a second time (2026-09-12: a
    # deploy worktree of swf-epicprod doubled it into 'other').
    repos = [d for d in sorted(GITHUB_ROOT.iterdir())
             if d.is_dir() and (d / '.git').is_dir()]
    for repo in repos:
        try:
            # -M enables rename detection so pure moves count ~zero lines
            # (with --no-renames, an in-repo rename booked every moved line
            # as new — a 2025-08 directory rename inflated primus by 21k).
            result = subprocess.run(
                ['git', '-c', 'safe.directory=*', 'log', 'HEAD',
                 f'--author={AUTHOR}', f'--since={WEEK_ZERO}T00:00:00',
                 '--numstat', '-M', '--format=@%H %ad',
                 '--date=format-local:%Y-%m-%d', *git_history_exclusions(repo)],
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
                commit_hash, _, date_str = line[1:].partition(' ')
                if commit_hash in CROSS_REPO_MOVE_COMMITS:
                    week = None
                    continue
                try:
                    week = _week_index(
                        datetime.strptime(date_str, '%Y-%m-%d').date())
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
