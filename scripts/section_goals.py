#!/usr/bin/env python3
"""Appends ## Goals to the daily synopsis entry."""
import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry


def build(since_ts, target_date):
    """Return markdown body or None. Lists goals created or modified in last 24h."""
    goals = Entry.objects.filter(
        kind='goal',
        deleted_at__isnull=True,
        timestamp_modified__gte=since_ts,
    ).order_by('-timestamp_modified')

    if not goals:
        return None

    lines = []
    for g in goals:
        data = g.data or {}
        entry_id = data.get('entry_id', '')
        title = g.content.split('\n', 1)[0].strip()
        created = g.timestamp_created >= since_ts
        label = 'new' if created else 'updated'
        ctx = f' :{g.context_id}' if g.context_id else ''
        pill = f' <a href="/tjai/entry/{g.id}" class="entry-pill">Entry</a>'
        if entry_id:
            link = f'[{title}](/tjai/entry/?entry_id={entry_id})'
        else:
            link = f'[{title}](/tjai/entry/{g.id})'
        lines.append(f'- {link} ({label}){ctx}{pill}')

    return '\n'.join(lines) if lines else None


if __name__ == '__main__':
    main_section('Goals', build)
