"""Journal/calendar command handlers for tj."""

import sys
from datetime import datetime, date, time, timedelta
from typing import Tuple, List, Optional

from tj.timezone_manager import get_current_timezone


def get_timezone_object():
    """Get timezone object for current user timezone.

    Returns pytz timezone if available, otherwise None (uses local time).
    """
    tz_name = get_current_timezone()

    try:
        import pytz
        return pytz.timezone(tz_name)
    except ImportError:
        # Fallback to local timezone if pytz not available
        return None
    except Exception:
        # Invalid timezone name, fallback to local
        return None


def parse_time(time_str: str) -> Tuple[int, int]:
    """Parse HH:MM time string.

    Args:
        time_str: Time in HH:MM format

    Returns:
        Tuple of (hour, minute)

    Raises:
        ValueError: If time format is invalid
    """
    if ':' not in time_str:
        raise ValueError(f"Time must be in HH:MM format, got: {time_str}")

    parts = time_str.split(':')
    if len(parts) != 2:
        raise ValueError(f"Time must be in HH:MM format, got: {time_str}")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time values in: {time_str}")

    if not (0 <= hour <= 23):
        raise ValueError(f"Hour must be 0-23, got: {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"Minute must be 0-59, got: {minute}")

    return hour, minute


def parse_date_spec(args_list: List[str]) -> Tuple[float, List[str]]:
    """Parse date/time specification from start of arguments list.

    Supports:
    - HH:MM (today at specified time)
    - tomorrow (tomorrow at midnight)
    - mon/tue/wed/thu/fri/sat/sun (next occurrence of weekday)
    - mmdd (4 digits, date in current year)
    - YYYYMMDD (8 digits, full date)
    - Any of above + HH:MM as second arg

    Args:
        args_list: List of argument strings

    Returns:
        Tuple of (event_timestamp_utc, remaining_args)
    """
    tz = get_timezone_object()

    # Get current datetime in user's timezone
    if tz:
        now = datetime.now(tz)
    else:
        now = datetime.now()

    if not args_list:
        # No date spec = now
        return now.timestamp(), args_list

    first = args_list[0].lower()
    remaining = args_list[1:]

    # Day name mappings
    day_names = {
        'mon': 0, 'monday': 0,
        'tue': 1, 'tuesday': 1,
        'wed': 2, 'wednesday': 2,
        'thu': 3, 'thursday': 3,
        'fri': 4, 'friday': 4,
        'sat': 5, 'saturday': 5,
        'sun': 6, 'sunday': 6
    }

    # Check for day names
    if first in day_names:
        target_day = day_names[first]
        today = now.date()
        current_day = today.weekday()
        days_ahead = (target_day - current_day) % 7
        if days_ahead == 0:
            days_ahead = 7  # Next occurrence, not today
        event_date = today + timedelta(days=days_ahead)

        # Check if next arg is time
        if remaining and ':' in remaining[0]:
            try:
                hour, minute = parse_time(remaining[0])
                remaining = remaining[1:]
            except ValueError as e:
                print(f"Warning: {e}, using midnight", file=sys.stderr)
                hour, minute = 0, 0
        else:
            hour, minute = 0, 0

        if tz:
            dt = tz.localize(datetime.combine(event_date, time(hour, minute)))
        else:
            dt = datetime.combine(event_date, time(hour, minute))

        return dt.timestamp(), remaining

    # Check for 'tomorrow'
    if first == 'tomorrow':
        tomorrow = now.date() + timedelta(days=1)

        # Check for time
        if remaining and ':' in remaining[0]:
            try:
                hour, minute = parse_time(remaining[0])
                remaining = remaining[1:]
            except ValueError as e:
                print(f"Warning: {e}, using midnight", file=sys.stderr)
                hour, minute = 0, 0
        else:
            hour, minute = 0, 0

        if tz:
            dt = tz.localize(datetime.combine(tomorrow, time(hour, minute)))
        else:
            dt = datetime.combine(tomorrow, time(hour, minute))

        return dt.timestamp(), remaining

    # Check for time only (HH:MM)
    if ':' in first:
        try:
            hour, minute = parse_time(first)
            today = now.date()

            if tz:
                dt = tz.localize(datetime.combine(today, time(hour, minute)))
            else:
                dt = datetime.combine(today, time(hour, minute))

            return dt.timestamp(), remaining
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            # Not a valid time, treat as content
            return now.timestamp(), args_list

    # Check for mmdd (4 digits, no colon)
    if first.isdigit() and len(first) == 4:
        try:
            month = int(first[0:2])
            day = int(first[2:4])
            year = now.year
            event_date = date(year, month, day)

            # Check for time as next arg
            if remaining and ':' in remaining[0]:
                try:
                    hour, minute = parse_time(remaining[0])
                    remaining = remaining[1:]
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0
            else:
                hour, minute = 0, 0

            if tz:
                dt = tz.localize(datetime.combine(event_date, time(hour, minute)))
            else:
                dt = datetime.combine(event_date, time(hour, minute))

            return dt.timestamp(), remaining
        except ValueError as e:
            print(f"Error: Invalid date in mmdd format '{first}': {e}", file=sys.stderr)
            return now.timestamp(), args_list

    # Check for YYYYMMDD (8 digits)
    if first.isdigit() and len(first) == 8:
        try:
            year = int(first[0:4])
            month = int(first[4:6])
            day = int(first[6:8])
            event_date = date(year, month, day)

            # Check for time as next arg
            if remaining and ':' in remaining[0]:
                try:
                    hour, minute = parse_time(remaining[0])
                    remaining = remaining[1:]
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0
            else:
                hour, minute = 0, 0

            if tz:
                dt = tz.localize(datetime.combine(event_date, time(hour, minute)))
            else:
                dt = datetime.combine(event_date, time(hour, minute))

            return dt.timestamp(), remaining
        except ValueError as e:
            print(f"Error: Invalid date in YYYYMMDD format '{first}': {e}", file=sys.stderr)
            return now.timestamp(), args_list

    # No date spec recognized, use now
    return now.timestamp(), args_list


def handle_journal(args) -> None:
    """Handle journal entry creation with flexible date parsing.

    Parses date/time from beginning of args.input and creates
    a calendar entry with the specified event_date.
    """
    from tj.commands.create import handle_creation

    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for journal entry.", file=sys.stderr)
        return

    try:
        # Parse date/time specification
        event_timestamp, remaining_args = parse_date_spec(args.input)

        if not remaining_args:
            print("Error: Journal entry must have content after date/time.", file=sys.stderr)
            return

        # Create args object for handle_creation
        class JournalArgs:
            def __init__(self):
                self.input = remaining_args
                self.timestamp_override = None  # Not used for journal
                self.event_date_override = event_timestamp

        journal_args = JournalArgs()

        # Call handle_creation with calendar type override
        handle_creation(journal_args, entry_type_override='calendar')

    except Exception as e:
        print(f"Error creating journal entry: {e}", file=sys.stderr)
