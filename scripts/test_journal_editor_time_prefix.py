#!/usr/bin/env python3
"""Regression checks for journal web-editor date/time prefixes."""

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tjai_app.journal_editor import parse_journal_editor_prefix


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main():
    tz = ZoneInfo("America/New_York")
    current = datetime(2026, 4, 29, 14, 0, tzinfo=tz).timestamp()

    content, ts = parse_journal_editor_prefix(
        "8:30am ePIC S&C workshop - discoverability",
        current,
        tz,
    )
    assert_equal(content, "ePIC S&C workshop - discoverability", "time-only content")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 4, 29, 8, 30), "time-only timestamp")

    content, ts = parse_journal_editor_prefix(
        "20260501/9am ePIC S&C workshop - discoverability",
        current,
        tz,
    )
    assert_equal(content, "ePIC S&C workshop - discoverability", "date/time content")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 5, 1, 9, 0), "date/time timestamp")

    content, ts = parse_journal_editor_prefix("8:30am title without current date", None, tz)
    assert_equal(content, "8:30am title without current date", "time-only without date content")
    assert_equal(ts, None, "time-only without date timestamp")

    print("journal editor time prefix tests passed")


if __name__ == "__main__":
    main()
