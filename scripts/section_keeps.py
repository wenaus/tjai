#!/usr/bin/env python3
"""Appends ## Keeps to the daily synopsis entry."""
import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry


def build(since_ts, target_date):
    """Return markdown body or None."""
    from django.db.models import Q
    entries = list(Entry.objects.filter(
        timestamp_modified__gte=since_ts,
        deleted_at__isnull=True,
        kind='bookmark',
    ).exclude(
        Q(context_id='picks') & ~Q(data__kept=True)
    ).order_by('-timestamp_modified').values_list('id', 'content'))
    if not entries:
        return None
    lines = []
    for entry_id, content in entries:
        first, *rest = content.split('\n', 1)
        pill = f' <a href="/tjai/entry/{entry_id}" class="entry-pill">Entry</a>'
        lines.append(f'- {first}{pill}')
        if rest and rest[0].strip():
            lines.append(f'  - {rest[0].strip()}')
    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('Keeps', build)
