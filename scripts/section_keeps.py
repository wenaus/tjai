#!/usr/bin/env python3
"""Appends ## Keeps to the daily synopsis entry."""
import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

from tjai_app.models import Entry


def build(since_ts, target_date):
    """Return markdown body or None."""
    kept = list(Entry.objects.filter(
        timestamp_modified__gte=since_ts,
        deleted_at__isnull=True,
        kind='bookmark',
        data__kept=True,
    ).order_by('-timestamp_modified').values_list('content', flat=True))
    if not kept:
        return None
    return '\n'.join(f'- {item}' for item in kept)


if __name__ == '__main__':
    main_section('Keeps', build)
