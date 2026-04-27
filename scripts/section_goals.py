#!/usr/bin/env python3
"""Appends ## Goals to the daily synopsis entry."""
from datetime import datetime, timedelta

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry
from tjai_app.services import get_timezone


def build(since_ts, target_date):
    """Return markdown body. Lists goals created or modified in the past 60 days."""
    del since_ts
    goal_since = datetime.combine(target_date - timedelta(days=60), datetime.min.time()).timestamp()

    goals = Entry.objects.filter(
        kind='goal',
        deleted_at__isnull=True,
        timestamp_modified__gte=goal_since,
    ).order_by('-timestamp_modified')

    if not goals:
        return 'No activity in past 60 days'

    lines = []
    tz = get_timezone()
    for g in goals:
        data = g.data or {}
        entry_id = data.get('entry_id', '')
        title = g.content.split('\n', 1)[0].strip()
        modified_date = datetime.fromtimestamp(g.timestamp_modified, tz).date()
        days_since_mod = max((target_date - modified_date).days, 0)
        day_label = 'day' if days_since_mod == 1 else 'days'
        ctx = f' :{g.context_id}' if g.context_id else ''
        pill = f' <a href="/tjai/entry/{g.id}" class="entry-pill">Entry</a>'
        if entry_id:
            link = f'[{title}](/tjai/entry/?entry_id={entry_id})'
        else:
            link = f'[{title}](/tjai/entry/{g.id})'
        lines.append(f'- {link} ({days_since_mod} {day_label}){ctx}{pill}')

    return '\n'.join(lines) if lines else None


if __name__ == '__main__':
    main_section('Goals', build)
