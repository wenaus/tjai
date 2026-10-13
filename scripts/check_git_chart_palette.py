#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["coloraide>=4"]
# ///
"""Check the git activity chart's palette: neighbouring projects stay apart.

The weekly bars stack projects in PROJECTS order (scripts/cron/
refresh_git_weekly_loc.py) and the pie draws them clockwise in the same
order, the last slice meeting the first. Each neighbouring pair is
compared by CIEDE2000 colour difference under normal vision and under
simulated protanopia, deuteranopia and tritanopia, and every colour by
its difference from the page's dark surface. A pair below MIN_PAIR, or
a colour below MIN_SURFACE, is flagged and the exit status is 1.

Run with uv, which supplies the declared dependency:
    uv run scripts/check_git_chart_palette.py
"""
import ast
import re
import sys
from pathlib import Path

from coloraide import Color

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'tjai_app/templates/tjai_app/git_activity.html'
WEEKLY = ROOT / 'scripts/cron/refresh_git_weekly_loc.py'
SURFACE = '#1a1a1a'
VISIONS = ('normal', 'protan', 'deutan', 'tritan')
MIN_PAIR = 4.4      # the swf family's subtle shades, the closest neighbours that read apart
MIN_SURFACE = 25.0  # every colour reads clearly on the dark surface


def projects():
    tree = ast.parse(WEEKLY.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) == 'PROJECTS' for t in node.targets):
            return ast.literal_eval(node.value)
    sys.exit(f'PROJECTS not found in {WEEKLY}')


def palette():
    text = TEMPLATE.read_text()
    block = re.search(r'const LOC_COLORS = \{(.*?)\n\};', text, re.S)
    if not block:
        sys.exit(f'LOC_COLORS not found in {TEMPLATE}')
    return dict(re.findall(r"'([^']+)':\s*'(#[0-9a-fA-F]{6})'", block.group(1)))


def seen(hex_colour, vision):
    c = Color(hex_colour)
    return c if vision == 'normal' else c.filter(vision, method='brettel')


def main():
    order, colours = projects(), palette()
    missing = [p for p in order if p not in colours]
    if missing:
        sys.exit(f'no colour for: {", ".join(missing)}')
    failures = 0
    print(f'{"neighbours":40s}' + ''.join(f'{v:>8s}' for v in VISIONS))
    for a, b in zip(order, order[1:] + order[:1]):
        des = [seen(colours[a], v).delta_e(seen(colours[b], v), method='2000')
               for v in VISIONS]
        low = min(des) < MIN_PAIR
        failures += low
        print(f'{a + " / " + b:40s}' + ''.join(f'{d:8.1f}' for d in des)
              + ('   LOW' if low else ''))
    print(f'\n{"on " + SURFACE:40s}')
    for p in order:
        d = min(seen(colours[p], v).delta_e(Color(SURFACE), method='2000')
                for v in VISIONS)
        low = d < MIN_SURFACE
        failures += low
        print(f'{p:40s}{d:8.1f}' + ('   LOW' if low else ''))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
