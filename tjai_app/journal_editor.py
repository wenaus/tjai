"""Helpers for journal entry edits made through the web editor."""

import re
from datetime import datetime

from tj.commands.journal import parse_time


_DATE_PREFIX_RE = re.compile(r'^(\d{8})(?:/(\S+))?\s+(.*)', re.DOTALL)
_TOKEN_PREFIX_RE = re.compile(r'^(\S+)\s+(.*)', re.DOTALL)


def parse_journal_editor_prefix(content, current_event_ts, tz):
    """Parse date/time prefixes from web-edited journal content.

    Returns (stripped_content, event_timestamp) when a prefix is recognized.
    Returns (original_content, None) when no date/time edit was requested.
    """
    m = _DATE_PREFIX_RE.match(content)
    if m:
        date_str, time_str, rest = m.group(1), m.group(2), m.group(3)
        parsed_date = datetime.strptime(date_str, '%Y%m%d')
        hour, minute = (0, 0)
        if time_str:
            hour, minute = parse_time(time_str)
        dt = parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)
        return rest, dt.timestamp()

    if current_event_ts is None:
        return content, None

    m = _TOKEN_PREFIX_RE.match(content)
    if not m:
        return content, None

    time_str, rest = m.group(1), m.group(2)
    hour, minute = parse_time(time_str)
    current_dt = datetime.fromtimestamp(float(current_event_ts), tz=tz)
    dt = current_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return rest, dt.timestamp()
