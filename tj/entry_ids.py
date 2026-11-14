"""Readable entry_id rules shared by every path that copies an entry.

A copied entry must not carry its source's data.entry_id: lookups by
entry_id return one entry, so a duplicate hides one of the two.
"""

import re
from datetime import date, datetime
from typing import Callable, Optional

_TRAILING_DATE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{8})$')


def _parse_stamp(stamp: str) -> Optional[date]:
    fmt = '%Y-%m-%d' if '-' in stamp else '%Y%m%d'
    try:
        return datetime.strptime(stamp, fmt).date()
    except ValueError:
        return None


def copied_entry_id(source_entry_id: Optional[str], target_date: date,
                    exists: Optional[Callable[[str], bool]] = None) -> Optional[str]:
    """Return the entry_id for a copy of an entry placed on target_date.

    A trailing date stamp (YYYYMMDD or YYYY-MM-DD) is replaced by target_date
    in the same form; otherwise -YYYYMMDD is appended. A source without an
    entry_id gives None. When exists is given, -2, -3, ... is appended until
    exists(candidate) is false.
    """
    if not source_entry_id:
        return None
    match = _TRAILING_DATE.search(source_entry_id)
    if match and _parse_stamp(match.group(1)):
        stamp = match.group(1)
        fmt = '%Y-%m-%d' if '-' in stamp else '%Y%m%d'
        candidate = source_entry_id[:match.start()] + target_date.strftime(fmt)
    else:
        candidate = f"{source_entry_id}-{target_date.strftime('%Y%m%d')}"
    if exists is None:
        return candidate
    unique, n = candidate, 2
    while exists(unique):
        unique = f"{candidate}-{n}"
        n += 1
    return unique
