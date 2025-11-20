"""Calendar view command for tj."""

import sys
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

from tj.colors import colorize_timestamp, colorize_content
from tj.repository_factory import RepositoryFactory
from tj.commands.common import format_entry_for_display
from tj.timezone_manager import get_current_timezone
from zoneinfo import ZoneInfo


def parse_timeframe(timeframe: Optional[str]) -> Tuple[str, float, float]:
    """Parse timeframe argument and return (description, start_timestamp, end_timestamp).

    Supports:
    - None -> this week (Monday-Sunday), same as 'w'
    - N (number) -> next N days from today (N>0) or last N days (N<0)
    - 't' -> today only
    - 'w' -> this week (Monday-Sunday)
    - 'm' -> this month
    - 't+N', 't-N' -> N days forward/back
    - 'w+N', 'w-N' -> N weeks forward/back
    - 'm+N', 'm-N' -> N months forward/back
    """
    tz_name = get_current_timezone()
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    # Default (no arg) shows configured number of days (default 30)
    if not timeframe:
        from tj.config import get_calendar_default_days
        timeframe = str(get_calendar_default_days())

    # Check for bare number (e.g., "30" or "-30")
    if timeframe.isdigit() or (timeframe.startswith('-') and timeframe[1:].isdigit()):
        days = int(timeframe)
        if days > 0:
            # Next N days
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=days)
            desc = f"Next {days} days" if days > 1 else "Next day"
        else:
            # Last N days (days is negative)
            end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            start = end + timedelta(days=days)
            desc = f"Last {abs(days)} days" if abs(days) > 1 else "Yesterday"
        return desc, start.timestamp(), end.timestamp()

    # Explicit 't' shows only today
    if timeframe == 't':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return "Today", start.timestamp(), end.timestamp()

    # Parse offset if present
    offset_pattern = re.match(r'^([twm])([+-]\d+)$', timeframe)
    if offset_pattern:
        unit = offset_pattern.group(1)
        offset = int(offset_pattern.group(2))
    else:
        unit = timeframe
        offset = 0

    if unit == 't':
        # Day with offset
        target_day = now + timedelta(days=offset)
        start = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        if offset == 0:
            desc = "Today"
        elif offset == 1:
            desc = "Tomorrow"
        elif offset == -1:
            desc = "Yesterday"
        elif offset > 0:
            desc = f"{offset} days from now"
        else:
            desc = f"{abs(offset)} days ago"

        return desc, start.timestamp(), end.timestamp()

    elif unit == 'w':
        # Week with offset - different behavior based on offset
        if offset == 0:
            # This week only
            target_week_start = now - timedelta(days=now.weekday())
            start = target_week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            desc = "This week"
        elif offset > 0:
            # Next N weeks (starting from today)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(weeks=offset)
            desc = f"Next {offset} weeks" if offset > 1 else "Next week"
        else:
            # Previous N weeks (ending today)
            end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            start = end + timedelta(weeks=offset)
            desc = f"Previous {abs(offset)} weeks" if abs(offset) > 1 else "Last week"

        return desc, start.timestamp(), end.timestamp()

    elif unit == 'm':
        # Month with offset
        target_month = now.month + offset
        target_year = now.year

        while target_month < 1:
            target_month += 12
            target_year -= 1
        while target_month > 12:
            target_month -= 12
            target_year += 1

        start = now.replace(year=target_year, month=target_month, day=1, hour=0, minute=0, second=0, microsecond=0)

        # Calculate end of month
        if target_month == 12:
            end = start.replace(year=target_year + 1, month=1, day=1)
        else:
            end = start.replace(month=target_month + 1, day=1)

        if offset == 0:
            desc = "This month"
        elif offset == 1:
            desc = "Next month"
        elif offset == -1:
            desc = "Last month"
        elif offset > 0:
            desc = f"{offset} months from now"
        else:
            desc = f"{abs(offset)} months ago"

        return desc, start.timestamp(), end.timestamp()

    else:
        print(f"Error: Invalid timeframe '{timeframe}'. Use t/w/m with optional +N/-N", file=sys.stderr)
        sys.exit(1)


def handle_calendar_view(args) -> None:
    """View calendar entries for specified timeframe."""
    from tj.state import display_context
    display_context()
    try:
        repository = RepositoryFactory.get_repository()

        # Parse timeframe - combine with offset if provided
        timeframe = args.timeframe if hasattr(args, 'timeframe') else None
        offset = args.offset if hasattr(args, 'offset') and args.offset else None

        # If offset provided separately, combine with timeframe
        if offset and timeframe in ['t', 'w', 'm']:
            try:
                # Support both positive and negative numbers
                offset_num = int(offset)
                timeframe = f"{timeframe}{offset_num:+d}"
            except ValueError:
                print(f"Error: Invalid offset '{offset}'. Must be a number.", file=sys.stderr)
                return

        desc, start_ts, end_ts = parse_timeframe(timeframe)

        # Get timezone for display
        tz_name = get_current_timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = None

        # Query calendar entries
        all_calendar = repository.query_entries(kind='calendar')

        # Filter by event_date within timeframe and group by date
        entries_by_date = {}
        for entry in all_calendar:
            if entry.data and 'event_date' in entry.data:
                event_ts = entry.data['event_date']
                if start_ts <= event_ts < end_ts:
                    # Get date for grouping
                    if tz:
                        event_dt = datetime.fromtimestamp(event_ts, tz=tz)
                    else:
                        event_dt = datetime.fromtimestamp(event_ts)
                    date_key = event_dt.strftime('%Y%m%d')

                    if date_key not in entries_by_date:
                        entries_by_date[date_key] = []
                    entries_by_date[date_key].append((event_ts, event_dt, entry))

        if not entries_by_date:
            print(f"No calendar entries for {desc}")
            return

        # Sort dates
        sorted_dates = sorted(entries_by_date.keys())

        # Determine if showing multiple days
        is_multi_day = (end_ts - start_ts) > (24 * 60 * 60 + 1)  # More than one day

        # Determine timeframe unit for header
        timeframe_str = args.timeframe if hasattr(args, 'timeframe') and args.timeframe else None
        unit = timeframe_str[0] if timeframe_str else None

        if tz:
            start_dt = datetime.fromtimestamp(start_ts, tz=tz)
        else:
            start_dt = datetime.fromtimestamp(start_ts)

        # Print header based on timeframe
        if unit == 'w':
            # For multi-week view, show date range instead of just week number
            end_dt = datetime.fromtimestamp(end_ts, tz=tz) if tz else datetime.fromtimestamp(end_ts)
            if (end_ts - start_ts) > (7 * 24 * 60 * 60):  # More than one week
                print(colorize_timestamp(f"{start_dt.strftime('%Y%m%d')}-{end_dt.strftime('%Y%m%d')} {desc}"))
            else:
                week_num = start_dt.isocalendar()[1]
                print(colorize_timestamp(f"{start_dt.strftime('%Y%m%d')} Week {week_num}"))
        elif unit == 'm':
            print(colorize_timestamp(f"{start_dt.strftime('%Y%m')} {start_dt.strftime('%B %Y')}"))
        elif is_multi_day:
            # Multi-day view (no explicit unit, default behavior)
            print(colorize_timestamp(desc))
        else:
            # Single day view
            print(colorize_timestamp(start_dt.strftime('%a %b %d, %Y')))

        # Print entries grouped by day
        # Get truncate length from config
        from tj.config import get_content_truncate_length
        truncate_len = get_content_truncate_length()

        last_week = None
        for date_key in sorted_dates:
            entries = entries_by_date[date_key]
            # Sort entries within day by time
            entries.sort(key=lambda x: x[0])

            # Determine if this is today's section
            event_date = entries[0][1].date()
            if tz:
                now_dt = datetime.now(tz)
                today = now_dt.date()
            else:
                now_dt = datetime.now()
                today = now_dt.date()
            is_today = (event_date == today)

            # Check for week boundary in multi-day views
            if is_multi_day:
                event_dt_for_week = entries[0][1]
                current_week = event_dt_for_week.isocalendar()[1]
                current_year = event_dt_for_week.year

                # Print week header when crossing week boundary or at start
                if last_week is None or (current_year, current_week) != last_week:
                    week_start = event_dt_for_week - timedelta(days=event_dt_for_week.weekday())
                    print(f"\n{colorize_timestamp(f'{week_start.strftime("%Y%m%d")} Week {current_week}')}")
                    last_week = (current_year, current_week)

            # Print day header for multi-day views
            if is_multi_day:
                day_name = entries[0][1].strftime('%a %b %d')
                if is_today:
                    from tj.colors import BOLD, RESET, TERRACOTTA
                    current_time = now_dt.strftime('%H:%M')
                    print(f"  {BOLD}{colorize_timestamp(f'{day_name} {current_time}')} {TERRACOTTA}{BOLD}Today{RESET}")
                else:
                    print(f"  {colorize_timestamp(day_name)}")

            # Print entries
            for event_ts, event_dt, entry in entries:
                # Truncate content if needed (by lines, before colorizing)
                # First line always shown + truncate_len additional lines
                content = entry.content
                lines = content.split('\n')
                if len(lines) > (truncate_len + 1):
                    content = '\n'.join(lines[:truncate_len + 1]) + " [...]"

                # Colorize content (converts markdown links to clickable terminal links)
                display_text = colorize_content(content)

                # Calculate countdown for today's future events
                countdown_str = ""
                if is_today and (event_dt.hour != 0 or event_dt.minute != 0):
                    time_diff = event_dt - now_dt
                    total_seconds = int(time_diff.total_seconds())
                    if total_seconds > 0:  # Event is in the future
                        from tj.colors import TERRACOTTA, RESET, BOLD
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        if hours > 0:
                            countdown_str = f" {TERRACOTTA}{BOLD}in {hours}h {minutes}m{RESET}"
                        else:
                            countdown_str = f" {TERRACOTTA}{BOLD}in {minutes}m{RESET}"

                # Handle multi-line content
                lines = display_text.split('\n')

                # Show time if not midnight
                if event_dt.hour != 0 or event_dt.minute != 0:
                    time_str = colorize_timestamp(event_dt.strftime('%H:%M'))
                    if is_multi_day:
                        first_indent = "    "
                        subsequent_indent = "      "
                    else:
                        first_indent = "  "
                        subsequent_indent = "    "

                    # Build first line with optional bold for today and countdown
                    if is_today:
                        from tj.colors import BOLD, RESET
                        first_line = f"{first_indent}{BOLD}{time_str}{countdown_str} {lines[0]}{RESET}"
                    else:
                        first_line = f"{first_indent}{time_str} {lines[0]}"

                    print(first_line)
                    # Print subsequent lines with extra indent and bold if today
                    for line in lines[1:]:
                        if is_today:
                            from tj.colors import BOLD, RESET
                            print(f"{subsequent_indent}{BOLD}{line}{RESET}")
                        else:
                            print(f"{subsequent_indent}{line}")
                else:
                    # Midnight = all-day event, no time shown
                    if is_multi_day:
                        first_indent = "    "
                        subsequent_indent = "      "
                    else:
                        first_indent = "  "
                        subsequent_indent = "    "

                    # Print with bold if today
                    if is_today:
                        from tj.colors import BOLD, RESET
                        print(f"{first_indent}{BOLD}{lines[0]}{RESET}")
                        for line in lines[1:]:
                            print(f"{subsequent_indent}{BOLD}{line}{RESET}")
                    else:
                        print(f"{first_indent}{lines[0]}")
                        for line in lines[1:]:
                            print(f"{subsequent_indent}{line}")

    except Exception as e:
        print(f"Calendar view error: {e}", file=sys.stderr)
