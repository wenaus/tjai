#!/usr/bin/env python3
"""Appends ## ToDo to the daily synopsis entry.

Lists todos added or modified in the last 30 days, reverse time order,
title as link to the entry detail page with up to 3 lines of subtext.
All statuses included.
"""
import time

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry


LOOKBACK_DAYS = 30
SUBTEXT_LINES = 3
SUBTEXT_MAX_CHARS = 140


def _title_and_subtext(entry):
    """Return (title, [subtext_line, ...]) from an entry.

    Title: `entry.name` if set, else first non-empty content line with
    leading markdown heading markers stripped. Subtext: next N non-empty
    content lines, each truncated.
    """
    lines = [l.rstrip() for l in (entry.content or '').split('\n')]
    non_empty = [l for l in lines if l.strip()]

    if entry.name:
        title = entry.name
        body_lines = non_empty
    else:
        title = non_empty[0] if non_empty else '(untitled)'
        title = title.lstrip('#').strip() or '(untitled)'
        body_lines = non_empty[1:]

    subtext = []
    for l in body_lines:
        stripped = l.strip()
        if stripped.startswith('#'):
            continue
        if len(stripped) > SUBTEXT_MAX_CHARS:
            stripped = stripped[:SUBTEXT_MAX_CHARS - 1].rstrip() + '…'
        subtext.append(stripped)
        if len(subtext) >= SUBTEXT_LINES:
            break

    return title, subtext


def build(since_ts, target_date):
    """Return markdown body or None.

    Ignores the passed since_ts (24h default) — todos need a longer window
    to serve as a surfacing mechanism.
    """
    window_ts = time.time() - LOOKBACK_DAYS * 86400

    entries = list(Entry.objects.filter(
        kind='todo',
        deleted_at__isnull=True,
        timestamp_modified__gte=window_ts,
    ).order_by('-timestamp_modified'))

    if not entries:
        return None

    lines = []
    for entry in entries:
        title, subtext = _title_and_subtext(entry)
        ctx_suffix = f'   :{entry.context_id}' if entry.context_id else ''
        link = f'/tjai/entry/{entry.id}'
        lines.append(f'- [{title}]({link}){ctx_suffix}')
        for st in subtext:
            lines.append(f'  - {st}')
    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('ToDo', build)
