#!/usr/bin/env python3
"""Regenerate data/git_weekly_loc.json — weekly lines-added by project.

Feeds the stacked-bar chart below the git grid on the git activity page.
Counts lines added (git numstat) in commits authored by AUTHOR across all
git repositories under GITHUB_ROOT, bucketed by ISO week (Monday start,
tjai timezone) from WEEK_ZERO onward, attributed to a project by repo —
and for tjrepo by top-level directory.

Runs via cron; the page's data endpoint just reads the JSON file.
"""
import ast
import json
import logging
import os
import re
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
            'swf-epicprod', 'swf-testbed', 'primus',
            'etaverse-core', 'etaverse-client', 'etaverse-building',
            'etaverse-avatars', 'etaverse-vehicles', 'etaverse-rl',
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
    'etaverse': 'etaverse-client', 'kozykorner': 'dev',  # etaverse: by subject, below
    'pax-eden': 'pax-eden', 'primus': 'primus', 'primus-blender': 'primus',
    'primus-sl': 'primus', 'primus-wright': 'primus',
    'blender': 'blender',
    'sl': 'primus', 'lsl': 'primus', 'lslp': 'primus', 'wright': 'primus',
    'computers': 'dev', 'torre-code': 'dev', 'ops': 'dev', 'docs': 'dev',
    'talks': 'dev', '.claude': 'dev', '.devcontainer': 'dev',
    '.vscode': 'dev', '': 'dev',
}

# Etaverse is factored by subject, as the swf family is by repository. Its
# directories divide by layer (client, server, scripts, docs), so a file's
# subject is read from its etaverse-relative path: the first pattern that
# matches names the project, and an unmatched file belongs to the world
# client (scene, rendering, terrain, sky, physics, camera, navigation, the
# control window and the V1 client). A new module whose name carries none
# of its subject's words is added to its pattern here; the next run
# recomputes every week from history.
_B = r'(?:^|[/_.-])'  # the word starts at a path or name boundary
ETAVERSE_SUBJECTS = tuple((project, re.compile(pattern)) for project, pattern in (
    # RL: the Earth atlas, the catalogue stars, astronomy and the voyage
    ('etaverse-rl', _B + r'earth|astronom|star-(?:field|glow|body|model)|sky_stars'
     r'|moon-disc|voyage-(?:control|places|card|url)|atlas-journey|^docs/sky\.md$'),
    ('etaverse-vehicles', _B + r'(?:flight|vessel|rail|ship|boat|helm|dock'
     r'|thunderbird|maglev|air.taxi|yacht|vehicle|tender|slipway|landing'
     r'|ride-crossing|realm.crossing|rolling-wheels)'),
    ('etaverse-avatars', _B + r'(?:avatar|attire|wear|outfit|hair|bento|plushead'
     r'|mixamo|reallusion|poses?|seats?|sits?|anim|alpha-panel|shape-panel'
     r'|looks|appearance|worn|stance|avsitter|kimmy)'),
    # building: houses, structures, furniture, families, finishes, smart
    # objects, editing, and the built places (Trantor, the Valley Forge,
    # Arrakis, the shellworld)
    ('etaverse-building', _B + r'(?:structure|house|famil|furniture|finish|kinds'
     r'|piece|component|wall.panel|editing|edit-panel|refinement|layouts'
     r'|materials?\.|asset-materials|covering|candle|faucet|tap|lamps?|fountain'
     r'|doors?|kitchen|ranch|gable|prairie|place.family|scatter|smart-objects'
     r'|modular|inworld-builds|frame-panel|construction|trantor|valley.forge'
     r'|arrakis|shellworld|sandworm)'),
    # core: the server and federation
    ('etaverse-core', r'^(?:world/(?!static/|templates/)|hub/|deploy/|agent/'
     r'|etaverse_project/|[^/]+$)|federation|persistence|inventory|asset.store'
     r'|resident-state|schema|deployment'),
))


def _etaverse_subject(path):
    """Project for an etaverse-relative path: its subject, else the client."""
    for project, rx in ETAVERSE_SUBJECTS:
        if rx.search(path):
            return project
    return 'etaverse-client'

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

# Cross-repo moves: git rename detection (-M) is per-repo, so code moved
# between repositories books as new lines. Enumerated manually and dropped.
CROSS_REPO_MOVE_COMMITS = {
    # pcs application + docs moved swf-monitor -> swf-epicprod (25k lines)
    'affc90be498c4f7c8aa2da96c52f24f14faf6883',
}


# Generated output and imported material, by explicit tjrepo-relative path.
# Authored definitions, presets, catalogs and configuration count regardless
# of format. '*' stays within one directory; '**' crosses directories.
DATA_PATHS = {
    'tjrepo': (
        'data/recipes*.xml',  # Yum recipe exports, not source code
        'data/myrecipes.xml',
        'data/newrecipes*.xml',
        'tjai/recipe_data/**',  # imported recipe collection
        'computers/common/claude-skills/synced/**',
        'primus-blender/factory/maglev/meadow-source.json',
        'primus-blender/factory/**/output/**',
        'etaverse/frontend/src/world/epicprod-evidence.json',
        'etaverse/scripts/data/trantor-rail-assessment.json',
        'etaverse/output/**',
        'etaverse/frontend/src/shared/avatars/sl-shape-data.json',
        'etaverse/frontend/src/shared/avatars/sl-shape-sliders.json',
        # Catalogs generated from IAU features and JPL orbital states.
        'etaverse/assets/astronomy/charon-marks.json',
        'etaverse/assets/astronomy/earth-spacecraft.json',
        'etaverse/assets/astronomy/europa-marks.json',
        'etaverse/assets/astronomy/jupiter-marks.json',
        'etaverse/assets/astronomy/pluto-marks.json',
        'etaverse/assets/astronomy/saturn-marks.json',
        'etaverse/assets/astronomy/titan-marks.json',
        'etaverse/assets/astronomy/venus-marks.json',
        'etaverse/assets/mars/features.json',
        'etaverse/assets/mars/system.json',
        # Generated/imported Earth publication bundles. Authored *-selection.json
        # inputs alongside them and the Python builders remain counted.
        'etaverse/assets/earth/curation/mappeteer.json',
        'etaverse/assets/earth/curation/appalachia.json',
        'etaverse/assets/earth/curation/berkshires-bookmarks.json',
        'etaverse/assets/earth/curation/loves-roadtrip.json',
        'etaverse/assets/earth/curation/texas-roadhouse-roadtrip.json',
        'etaverse/assets/earth/curation/national-parks-roadtrip.json',
        'etaverse/assets/earth/curation/cultural-highlights/*.json',
        'etaverse/assets/bento/sl/*.xml',  # imported Second Life data
        'etaverse/assets/bento/vendor/**',  # third-party code and data
        'etaverse/docs/history/*.json',  # generated change inventories
    ),
}

def _glob_regex(pattern):
    out = ''
    i = 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif pattern.startswith('**', i):
            out, i = out + '.*', i + 2
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r'\Z')


DATA_REGEXES = {repo: tuple(_glob_regex(p) for p in patterns)
                for repo, patterns in DATA_PATHS.items()}


def _generated_family_paths(repo):
    """Read the family writer's registry without executing its generators."""
    source_dir = repo / 'etaverse' / 'scripts' / 'families'
    registry = source_dir / '__init__.py'
    if not registry.exists():
        return set()
    tree = ast.parse(registry.read_text(encoding='utf-8'))
    modules = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == 'MODULES'
                       for target in node.targets))
    family_names = {path.stem for path in
                    (repo / 'etaverse' / 'world' / 'families').glob('*.json')}
    generated = set()
    for module in modules:
        tree = ast.parse((source_dir / f'{module}.py').read_text(encoding='utf-8'))
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        candidates = literals & family_names
        conventional_name = module.replace('_', '-')
        if conventional_name in family_names or not candidates:
            name = conventional_name
        elif len(candidates) == 1:
            name = candidates.pop()
        else:
            logger.warning('Unresolved family output for %s; keeping it counted', module)
            continue
        generated.add(f'etaverse/world/families/{name}.json')
    return generated


GENERATED_FAMILY_PATHS = _generated_family_paths(GITHUB_ROOT / 'tjrepo')


def _excluded(path, repo_name=''):
    if any(part in path for part in EXCLUDE_PARTS):
        return True
    if path.endswith(EXCLUDE_SUFFIXES):
        return True
    if repo_name == 'tjrepo' and path in GENERATED_FAMILY_PATHS:
        return True
    return any(rx.match(path) for rx in DATA_REGEXES.get(repo_name, ()))


def _project_for(repo_name, path):
    if repo_name == 'tjrepo':
        top = path.split('/', 1)[0] if '/' in path else ''
        if top in EXCLUDE_TJREPO_TOP:
            return None
        if top == 'etaverse':
            return _etaverse_subject(path.split('/', 1)[1])
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
            if _excluded(path, repo.name):
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
        'bulk_commit_lines': None,  # Retained field; authored work has no size cutoff.
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
