"""Shared date parsing utilities for tjai."""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

from .timezone_manager import get_timezone_object


def parse_date_filter(date_str: str, default_days_ago: int = None, end_of_day: bool = False) -> Tuple[Optional[float], Optional[str]]:
    """Parse a date string for filtering queries.

    Supports:
    - None/empty: returns default_days_ago from now, or None if no default
    - "today": start of today (or end if end_of_day=True)
    - "yesterday": start of yesterday (or end if end_of_day=True)
    - "N days ago" or "Nd": N days ago
    - "last week": 7 days ago
    - Day names (mon, tuesday, etc.): most recent occurrence
    - YYYYMMDD: specific date
    - ISO format: specific datetime

    Args:
        date_str: Date specification string
        default_days_ago: If date_str is empty, return this many days ago
        end_of_day: If True, return 23:59:59 instead of 00:00:00 for date-only specs

    Returns:
        Tuple of (timestamp, error_string). On success error is None.
        On failure timestamp is None and error explains the problem.
    """
    tz = get_timezone_object()
    now = datetime.now(tz) if tz else datetime.now()

    def set_time(dt):
        """Set time to start or end of day based on end_of_day flag."""
        if end_of_day:
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if not date_str:
        if default_days_ago is not None:
            dt = set_time(now - timedelta(days=default_days_ago))
            return dt.timestamp(), None
        return None, None

    date_str = date_str.lower().strip()

    # "today"
    if date_str == "today":
        dt = set_time(now)
        return dt.timestamp(), None

    # "yesterday"
    if date_str == "yesterday":
        dt = set_time(now - timedelta(days=1))
        return dt.timestamp(), None

    # "last week"
    if date_str == "last week":
        dt = set_time(now - timedelta(days=7))
        return dt.timestamp(), None

    # "N days ago" or "Nd"
    match = re.match(r'^(\d+)\s*d(?:ays?)?\s*(?:ago)?$', date_str)
    if match:
        days = int(match.group(1))
        dt = set_time(now - timedelta(days=days))
        return dt.timestamp(), None

    # Day names
    day_names = {
        'mon': 0, 'monday': 0,
        'tue': 1, 'tuesday': 1,
        'wed': 2, 'wednesday': 2,
        'thu': 3, 'thursday': 3,
        'fri': 4, 'friday': 4,
        'sat': 5, 'saturday': 5,
        'sun': 6, 'sunday': 6
    }
    if date_str in day_names:
        target_day = day_names[date_str]
        current_day = now.weekday()
        # Find most recent occurrence (including today if it matches)
        days_back = (current_day - target_day) % 7
        dt = set_time(now - timedelta(days=days_back))
        return dt.timestamp(), None

    # YYYYMMDD
    if len(date_str) == 8 and date_str.isdigit():
        try:
            dt = datetime.strptime(date_str, '%Y%m%d')
            if tz:
                dt = dt.replace(tzinfo=tz)
            dt = set_time(dt)
            return dt.timestamp(), None
        except ValueError:
            return None, f"Invalid date '{date_str}'"

    # ISO format
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.timestamp(), None
    except ValueError:
        pass

    return None, f"Cannot parse date '{date_str}'. Use YYYYMMDD, 'yesterday', '3d', 'monday', etc."
