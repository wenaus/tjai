"""Journal/calendar command handlers for tj."""

import sys
import traceback
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Tuple, List, Optional

from tj.timezone_manager import get_current_timezone, get_timezone_object


def parse_time(time_str: str) -> Tuple[int, int]:
    """Parse time string in HH:MM or am/pm format.

    Args:
        time_str: Time in HH:MM (24-hour) or H:MMam/pm or Ham/pm format

    Returns:
        Tuple of (hour, minute)

    Raises:
        ValueError: If time format is invalid
    """
    import re

    # Check for am/pm format: 6pm, 6:30pm, 6am, 6:30am
    am_pm_match = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)$', time_str.lower())
    if am_pm_match:
        hour = int(am_pm_match.group(1))
        minute = int(am_pm_match.group(2)) if am_pm_match.group(2) else 0
        am_pm = am_pm_match.group(3)

        if not (1 <= hour <= 12):
            raise ValueError(f"Hour must be 1-12 for am/pm format, got: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"Minute must be 0-59, got: {minute}")

        # Convert to 24-hour
        if am_pm == 'am':
            if hour == 12:
                hour = 0
        else:  # pm
            if hour != 12:
                hour += 12

        return hour, minute

    # Check for HH:MM format (24-hour)
    if ':' not in time_str:
        raise ValueError(f"Time must be in HH:MM or am/pm format, got: {time_str}")

    parts = time_str.split(':')
    if len(parts) != 2:
        raise ValueError(f"Time must be in HH:MM or am/pm format, got: {time_str}")

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


def parse_date_spec(args_list: List[str], tz=None) -> Tuple[float, List[str]]:
    """Parse date/time specification from start of arguments list.

    Supports:
    - HH:MM (today at specified time)
    - tomorrow (tomorrow at midnight)
    - mon/tue/wed/thu/fri/sat/sun (next occurrence of weekday)
    - mmdd (4 digits, date in current year)
    - YYYYMMDD (8 digits, full date)
    - jan/feb/.../dec + day (month abbrev + day number)
    - Any of above + HH:MM as second arg

    Args:
        args_list: List of argument strings
        tz: Optional ZoneInfo to use. If None, resolved via the tj CLI
            timezone manager — which reads ~/.tjai/config.json and may
            prompt via input() if no location is configured. Non-CLI
            callers (web, daemons) MUST pass tz explicitly to avoid that
            interactive fallback.

    Returns:
        Tuple of (event_timestamp_utc, remaining_args)
    """
    if tz is None:
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

        # Check if next arg is time (HH:MM or am/pm format)
        if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
            try:
                hour, minute = parse_time(remaining[0])
                remaining = remaining[1:]
            except ValueError as e:
                print(f"Warning: {e}, using midnight", file=sys.stderr)
                hour, minute = 0, 0
        else:
            hour, minute = 0, 0

        if tz:
            dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
        else:
            dt = datetime.combine(event_date, time(hour, minute))

        return dt.timestamp(), remaining

    # Check for relative date specs: t+1, t-1, w+2, w-1, m+1, m-1
    import re
    relative_match = re.match(r'^([twm])([+-]\d+)$', first)
    if relative_match:
        unit = relative_match.group(1)
        offset_str = relative_match.group(2)
        offset = int(offset_str)

        if unit == 't':  # days
            target_date = now.date() + timedelta(days=offset)
        elif unit == 'w':  # weeks
            target_date = now.date() + timedelta(weeks=offset)
        elif unit == 'm':  # months (approximate as 30 days)
            target_date = now.date() + timedelta(days=offset * 30)

        # Check for time as next arg
        if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
            try:
                hour, minute = parse_time(remaining[0])
                remaining = remaining[1:]
            except ValueError as e:
                print(f"Warning: {e}, using midnight", file=sys.stderr)
                hour, minute = 0, 0
        else:
            hour, minute = 0, 0

        if tz:
            dt = datetime.combine(target_date, time(hour, minute, tzinfo=tz))
        else:
            dt = datetime.combine(target_date, time(hour, minute))

        return dt.timestamp(), remaining

    # Check for 'tomorrow' or 'yesterday'
    if first in ('tomorrow', 'yesterday'):
        offset = 1 if first == 'tomorrow' else -1
        target_date = now.date() + timedelta(days=offset)

        # Check for time
        if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
            try:
                hour, minute = parse_time(remaining[0])
                remaining = remaining[1:]
            except ValueError as e:
                print(f"Warning: {e}, using midnight", file=sys.stderr)
                hour, minute = 0, 0
        else:
            hour, minute = 0, 0

        if tz:
            dt = datetime.combine(target_date, time(hour, minute, tzinfo=tz))
        else:
            dt = datetime.combine(target_date, time(hour, minute))

        return dt.timestamp(), remaining

    # Check for slash-formatted dates FIRST (mm/dd, mm/dd/HH:MM, MMDD/HH:MM, yyyy/mm/dd, yyyy/mm/dd/HH:MM)
    # Must come before time-only check since mm/dd/HH:MM contains ':'
    if '/' in first and not first.startswith('http'):
        parts = first.split('/')
        try:
            if len(parts) == 2:
                # Could be mm/dd, yyyy/mm, MMDD/HH:MM, or YYYYMMDD/HH:MM
                if len(parts[0]) == 8 and parts[0].isdigit():
                    # YYYYMMDD/HH:MM format (e.g., 20251220/23:20)
                    year = int(parts[0][0:4])
                    month = int(parts[0][4:6])
                    day = int(parts[0][6:8])
                    event_date = date(year, month, day)

                    # Parse time from second part
                    try:
                        hour, minute = parse_time(parts[1])
                    except ValueError as e:
                        print(f"Warning: {e}, using midnight", file=sys.stderr)
                        hour, minute = 0, 0

                    if tz:
                        dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
                    else:
                        dt = datetime.combine(event_date, time(hour, minute))

                    return dt.timestamp(), remaining

                elif len(parts[0]) == 4:
                    # Could be yyyy/mm (date without day - invalid) or MMDD/HH:MM
                    # Check if second part looks like time (has : or is am/pm)
                    if ':' in parts[1] or parts[1].lower().endswith(('am', 'pm')):
                        # MMDD/HH:MM format (e.g., 1205/9:30)
                        month = int(parts[0][0:2])
                        day = int(parts[0][2:4])
                        year = now.year
                        event_date = date(year, month, day)

                        # Parse time from second part
                        try:
                            hour, minute = parse_time(parts[1])
                        except ValueError as e:
                            print(f"Warning: {e}, using midnight", file=sys.stderr)
                            hour, minute = 0, 0

                        if tz:
                            dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
                        else:
                            dt = datetime.combine(event_date, time(hour, minute))

                        return dt.timestamp(), remaining
                    else:
                        # yyyy/mm format (date without day - invalid)
                        pass  # Fall through to next check
                else:
                    # mm/dd format
                    month = int(parts[0])
                    day = int(parts[1])
                    year = now.year
                    event_date = date(year, month, day)

                    # Check for time as next arg
                    if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
                        try:
                            hour, minute = parse_time(remaining[0])
                            remaining = remaining[1:]
                        except ValueError as e:
                            print(f"Warning: {e}, using midnight", file=sys.stderr)
                            hour, minute = 0, 0
                    else:
                        hour, minute = 0, 0

                    if tz:
                        dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
                    else:
                        dt = datetime.combine(event_date, time(hour, minute))

                    return dt.timestamp(), remaining

            elif len(parts) == 3:
                # Could be mm/dd/HH:MM or yyyy/mm/dd
                if len(parts[0]) == 4:
                    # yyyy/mm/dd format
                    year = int(parts[0])
                    month = int(parts[1])
                    day = int(parts[2])
                    event_date = date(year, month, day)

                    # Check for time as next arg
                    if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
                        try:
                            hour, minute = parse_time(remaining[0])
                            remaining = remaining[1:]
                        except ValueError as e:
                            print(f"Warning: {e}, using midnight", file=sys.stderr)
                            hour, minute = 0, 0
                    else:
                        hour, minute = 0, 0
                else:
                    # mm/dd/HH:MM format
                    month = int(parts[0])
                    day = int(parts[1])
                    year = now.year
                    event_date = date(year, month, day)

                    # Parse time from third part
                    try:
                        hour, minute = parse_time(parts[2])
                    except ValueError as e:
                        print(f"Warning: {e}, using midnight", file=sys.stderr)
                        hour, minute = 0, 0

                if tz:
                    dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
                else:
                    dt = datetime.combine(event_date, time(hour, minute))

                return dt.timestamp(), remaining

            elif len(parts) == 4:
                # yyyy/mm/dd/HH:MM format
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                event_date = date(year, month, day)

                # Parse time from fourth part
                try:
                    hour, minute = parse_time(parts[3])
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0

                if tz:
                    dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
                else:
                    dt = datetime.combine(event_date, time(hour, minute))

                return dt.timestamp(), remaining

        except (ValueError, IndexError):
            # Not a valid date format, continue to next format check
            pass

    # Check for time only (HH:MM or am/pm format)
    if ':' in first or first.lower().endswith(('am', 'pm')):
        try:
            hour, minute = parse_time(first)
            today = now.date()

            if tz:
                dt = datetime.combine(today, time(hour, minute, tzinfo=tz))
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

            # Check for time as next arg (HH:MM or am/pm format)
            if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
                try:
                    hour, minute = parse_time(remaining[0])
                    remaining = remaining[1:]
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0
            else:
                hour, minute = 0, 0

            if tz:
                dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
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

            # Check for time as next arg (HH:MM or am/pm format)
            if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
                try:
                    hour, minute = parse_time(remaining[0])
                    remaining = remaining[1:]
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0
            else:
                hour, minute = 0, 0

            if tz:
                dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
            else:
                dt = datetime.combine(event_date, time(hour, minute))

            return dt.timestamp(), remaining
        except ValueError as e:
            print(f"Error: Invalid date in YYYYMMDD format '{first}': {e}", file=sys.stderr)
            return now.timestamp(), args_list

    # Check for month abbreviation + day (e.g., "mar 4", "jan 15")
    month_abbrs = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    if first in month_abbrs and remaining and remaining[0].isdigit():
        try:
            month = month_abbrs[first]
            day = int(remaining[0])
            remaining = remaining[1:]
            year = now.year
            event_date = date(year, month, day)

            # Check for time as next arg
            if remaining and (':' in remaining[0] or remaining[0].lower().endswith(('am', 'pm'))):
                try:
                    hour, minute = parse_time(remaining[0])
                    remaining = remaining[1:]
                except ValueError as e:
                    print(f"Warning: {e}, using midnight", file=sys.stderr)
                    hour, minute = 0, 0
            else:
                hour, minute = 0, 0

            if tz:
                dt = datetime.combine(event_date, time(hour, minute, tzinfo=tz))
            else:
                dt = datetime.combine(event_date, time(hour, minute))

            return dt.timestamp(), remaining
        except ValueError as e:
            print(f"Error: Invalid date '{first} {args_list[1]}': {e}", file=sys.stderr)
            return now.timestamp(), args_list

    # No date spec recognized, default to all-day event today (midnight)
    today = now.date()
    if tz:
        dt = datetime.combine(today, time(0, 0, tzinfo=tz))
    else:
        dt = datetime.combine(today, time(0, 0))
    return dt.timestamp(), args_list


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
        # Skip context markers at the beginning for date parsing
        input_args = args.input
        context_prefix = []
        while input_args and input_args[0].startswith('='):
            context_prefix.append(input_args[0])
            input_args = input_args[1:]

        # Parse date/time specification
        event_timestamp, remaining_args = parse_date_spec(input_args)

        # Add context back to remaining args
        remaining_args = context_prefix + remaining_args

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

        # Call handle_creation with journal type override
        handle_creation(journal_args, entry_type_override='journal')

    except Exception as e:
        traceback.print_exc()
        print(f"Error creating journal entry: {e}", file=sys.stderr)
