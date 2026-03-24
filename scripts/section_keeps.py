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
        Q(context_id='picks') & ~Q(data__kept=True) & ~Q(data__readme=True)
    ).prefetch_related('tags').order_by('-timestamp_modified'))
    if not entries:
        return None
    lines = []
    for entry in entries:
        first, *rest = entry.content.split('\n', 1)
        # Append tags/context not already visible in content text
        tag_strs = []
        if entry.context_id and f':{entry.context_id}' not in first:
            tag_strs.append(f':{entry.context_id}')
        for tag in entry.tags.all():
            if f':{tag.tag_name}' not in first:
                tag_strs.append(f':{tag.tag_name}')
        tag_suffix = '   ' + ' '.join(tag_strs) if tag_strs else ''
        pill = f' <a href="/tjai/entry/{entry.id}" class="entry-pill">Entry</a>'
        lines.append(f'- {first}{tag_suffix}{pill}')
        if rest and rest[0].strip():
            lines.append(f'  - {rest[0].strip()}')
    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('Keeps', build)
