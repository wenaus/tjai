#!/usr/bin/env python3
"""Check shared date-filter formats and bounds without database access."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tj.date_utils import parse_date_filter


tz = ZoneInfo("America/New_York")
instant = datetime(2026, 9, 10, 17, 8, tzinfo=timezone.utc).timestamp()

# Equivalent forms must denote the same instant, including as an upper bound.
for value in (
    "2026-09-10T17:08:00Z",
    "2026-09-10t17:08:00z",
    "  2026-09-10T17:08:00Z  ",
    "2026-09-10 17:08:00Z",
    "2026-09-10T17:08:00+00:00",
    "2026-09-10T13:08:00-04:00",
    "2026-09-10T22:38:00+05:30",
    "2026-09-10T13:08:00",
    "2026-09-10 13:08:00",
):
    for end_of_day in (False, True):
        result = parse_date_filter(value, end_of_day=end_of_day, tz=tz)
        assert result == (instant, None), (value, end_of_day, result)

fractional = parse_date_filter("2026-09-10T17:08:00.123456Z", end_of_day=True, tz=tz)
assert fractional == (instant + 0.123456, None), fractional

for value in ("20260910", "2026-09-10"):
    for end_of_day in (False, True):
        expected = datetime(2026, 9, 10, tzinfo=tz)
        if end_of_day:
            expected = expected.replace(hour=23, minute=59, second=59)
        assert parse_date_filter(value, end_of_day=end_of_day, tz=tz) == (expected.timestamp(), None)

now = datetime.now(tz)
for value, days in (("ToDaY", 0), ("yesterday", 1), ("3d", 3), ("3 days ago", 3), ("last week", 7), ("monday", now.weekday())):
    expected = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert parse_date_filter(value, tz=tz) == (expected.timestamp(), None), value

for value in ("2h", "2 hours ago"):
    before = (datetime.now(tz) - timedelta(hours=2)).timestamp()
    actual, error = parse_date_filter(value, end_of_day=True, tz=tz)
    after = (datetime.now(tz) - timedelta(hours=2)).timestamp()
    assert error is None and before <= actual <= after, (value, actual, error)

assert parse_date_filter(None, tz=tz) == (None, None)
for value in ("20260230", "2026-09-10T25:08:00Z", "not a date"):
    actual, error = parse_date_filter(value, tz=tz)
    assert actual is None and value in error, (value, actual, error)

print("Date-filter format and boundary checks passed.")
