#!/usr/bin/env python3
"""Regression checks for journal web-editor date/time prefixes."""

from datetime import datetime, timedelta
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

    content, ts, warnings = parse_journal_editor_prefix(
        "8:30am ePIC S&C workshop - discoverability",
        current,
        tz,
    )
    assert_equal(content, "ePIC S&C workshop - discoverability", "time-only content")
    assert_equal(warnings, [], "time-only warnings")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 4, 29, 8, 30), "time-only timestamp")

    content, ts, warnings = parse_journal_editor_prefix(
        "20260501/9am ePIC S&C workshop - discoverability",
        current,
        tz,
    )
    assert_equal(content, "ePIC S&C workshop - discoverability", "date/time content")
    assert_equal(warnings, [], "date/time warnings")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 5, 1, 9, 0), "date/time timestamp")

    # URL-after-date triggers a soft warning (parse_date_spec tries to read
    # the URL as a time and falls back to midnight). The user should see it.
    content, ts, warnings = parse_journal_editor_prefix(
        "20260620 [Bazaart](https://mackenzie.art/event/bazaart-2026/)",
        current,
        tz,
    )
    assert_equal(content, "[Bazaart](https://mackenzie.art/event/bazaart-2026/)", "date-only content")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 6, 20, 0, 0), "date-only all-day timestamp")
    if not warnings:
        raise AssertionError("expected a warning from URL-after-date case")

    content, ts, warnings = parse_journal_editor_prefix("8:30am title without current date", None, tz)
    assert_equal(content, "8:30am title without current date", "time-only without date content")
    assert_equal(ts, None, "time-only without date timestamp")
    assert_equal(warnings, [], "time-only without date warnings")

    # Natural language: "tomorrow 12:45 Pick up Mom"
    today_local = datetime.now(tz).date()
    content, ts, warnings = parse_journal_editor_prefix("tomorrow 12:45 Pick up Mom", None, tz)
    assert_equal(content, "Pick up Mom", "tomorrow content")
    assert_equal(warnings, [], "tomorrow warnings")
    dt = datetime.fromtimestamp(ts, tz)
    expected = today_local + timedelta(days=1)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                 (expected.year, expected.month, expected.day, 12, 45),
                 "tomorrow timestamp")

    # Natural language: "today 8:30am ..." on a new entry (no current date).
    # Regression: "today" was unrecognized, so the prefix was stored verbatim
    # with no event_date instead of anchoring to today at the given time.
    content, ts, warnings = parse_journal_editor_prefix("today 8:30am ePIC AC/SCC meeting", None, tz)
    assert_equal(content, "ePIC AC/SCC meeting", "today content")
    assert_equal(warnings, [], "today warnings")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                 (today_local.year, today_local.month, today_local.day, 8, 30),
                 "today timestamp")

    # mm/dd
    content, ts, warnings = parse_journal_editor_prefix("6/15 7pm dinner", None, tz)
    assert_equal(content, "dinner", "mm/dd content")
    assert_equal(warnings, [], "mm/dd warnings")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.month, dt.day, dt.hour, dt.minute), (6, 15, 19, 0), "mm/dd timestamp")

    # Month abbrev + day
    content, ts, warnings = parse_journal_editor_prefix("jan 5 9:00 kickoff", None, tz)
    assert_equal(content, "kickoff", "jan 5 content")
    assert_equal(warnings, [], "jan 5 warnings")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.month, dt.day, dt.hour, dt.minute), (1, 5, 9, 0), "jan 5 timestamp")

    # Non-date content: must pass through unchanged
    content, ts, warnings = parse_journal_editor_prefix("just some thoughts about life", None, tz)
    assert_equal(content, "just some thoughts about life", "non-date content")
    assert_equal(ts, None, "non-date timestamp")
    assert_equal(warnings, [], "non-date warnings")

    # Bad time after a valid date: warning must be surfaced
    content, ts, warnings = parse_journal_editor_prefix(
        "20260620 25:99 broken", None, tz
    )
    if not warnings:
        raise AssertionError("expected a warning from bad-time case")
    dt = datetime.fromtimestamp(ts, tz)
    assert_equal((dt.year, dt.month, dt.day), (2026, 6, 20), "bad-time falls back to midnight")

    print("journal editor time prefix tests passed")


if __name__ == "__main__":
    main()
