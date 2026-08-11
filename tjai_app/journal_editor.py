"""Helpers for journal entry edits made through the web editor."""

import io
import re
from contextlib import redirect_stderr
from datetime import datetime

from tj.commands.journal import parse_time, parse_date_spec


_TIME_ONLY_RE = re.compile(r'^(\d{1,2}:\d{2}|\d{1,2}(?::\d{2})?(?:am|pm))$', re.IGNORECASE)


def parse_journal_editor_prefix(content, current_event_ts, tz):
    """Parse a leading date/time spec from web-edited journal content.

    Accepts any prefix that `parse_date_spec` understands: YYYYMMDD,
    YYYYMMDD/HH:MM, mm/dd, yyyy/mm/dd, mmdd, today, tomorrow, yesterday,
    weekday names, t+N/w+N/m+N, jan/feb/.../dec + day. A trailing HH:MM
    or H[:MM]am|pm token is consumed as the time-of-day.

    Time-only prefix (e.g. "9am rest...") updates an existing event's
    time-of-day in place. For a new journal entry with no event date, it
    creates an event for today in the application timezone.

    Returns (stripped_content, event_timestamp, warnings). `warnings` is
    a list of human-readable strings emitted by `parse_date_spec` when
    it recovered from a soft failure (e.g. malformed time after a valid
    date); callers should surface them so the user can see when their
    intent was only partially honored.
    """
    if not content:
        return content, None, []

    first_line, sep, rest_lines = content.partition('\n')
    tokens = first_line.split()
    if not tokens:
        return content, None, []

    # Time-only prefix: preserve an existing date, or use today for a new
    # journal entry, then swap in the requested time.
    # parse_date_spec would interpret a bare HH:MM as "today at HH:MM",
    # which is not what the editor wants here.
    if _TIME_ONLY_RE.match(tokens[0]):
        try:
            hour, minute = parse_time(tokens[0])
        except ValueError:
            return content, None, []
        if current_event_ts is None:
            current_dt = datetime.now(tz)
        else:
            current_dt = datetime.fromtimestamp(float(current_event_ts), tz=tz)
        dt = current_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        new_first = ' '.join(tokens[1:])
        return _rejoin(new_first, sep, rest_lines), dt.timestamp(), []

    # Anything else: let parse_date_spec try. It returns the full args
    # list unchanged when nothing matches, so consumption (shorter
    # remaining) is the signal that a prefix was actually found.
    # parse_date_spec is CLI-oriented and prints diagnostic messages to
    # stderr on soft failures (bad time after a valid date, invalid
    # mmdd, etc.). Capture them so the caller can surface them to the
    # user instead of dropping them on the floor.
    err_buf = io.StringIO()
    try:
        with redirect_stderr(err_buf):
            ts, remaining = parse_date_spec(tokens, tz=tz)
    except (ValueError, TypeError, OverflowError, OSError):
        return content, None, []
    if len(remaining) >= len(tokens):
        return content, None, []

    warnings = [line for line in err_buf.getvalue().splitlines() if line.strip()]
    new_first = ' '.join(remaining)
    return _rejoin(new_first, sep, rest_lines), ts, warnings


def _rejoin(new_first, sep, rest_lines):
    if not sep:
        return new_first
    if not new_first:
        return rest_lines
    return new_first + sep + rest_lines
