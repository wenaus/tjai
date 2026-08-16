#!/usr/bin/env python3
"""Appends ## ToDo to the daily synopsis entry.

Lists all pending todos grouped by context under an explicit ### subsection
heading per context (uncontexted shown as "(no context)"). Both the
subsections and the todos within them are in reverse modification-time
order, so the context holding the most recently touched todo comes first.
Title as link to the entry detail page with up to 3 lines of subtext. Done
and archived todos are excluded; no time-depth limit.
"""
import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry


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

    Ignores the passed since_ts — all pending todos are surfaced regardless
    of age.
    """
    entries = list(Entry.objects.filter(
        kind='todo',
        deleted_at__isnull=True,
    ).exclude(status__in=('done', 'archive')).order_by('-timestamp_modified'))

    if not entries:
        return None

    # Single pass over the globally reverse-modified list: a context takes
    # its position from its most recently modified todo, and each context's
    # own todos stay in that same order.
    by_context = {}
    for entry in entries:
        by_context.setdefault(entry.context_id, []).append(entry)

    lines = []
    for ctx, ctx_entries in by_context.items():
        if lines:
            lines.append('')
        lines.append(f'### {ctx or "(no context)"}')
        for entry in ctx_entries:
            title, subtext = _title_and_subtext(entry)
            link = f'/tjai/entry/{entry.id}'
            lines.append(f'- [{title}]({link})')
            for st in subtext:
                lines.append(f'  - {st}')
    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('ToDo', build)
