"""Calendar view command for tj."""

import sys
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

from tj.colors import colorize_timestamp
from tj.repository_factory import RepositoryFactory
from tj.commands.common import format_entry_for_display
from tj.timezone_manager import get_current_timezone
from zoneinfo import ZoneInfo


def parse_timeframe(timeframe: Optional[str]) -> Tuple[str, float, float]:
    """Parse timeframe argument and return (description, start_timestamp, end_timestamp).

    Supports:
    - None, 't' -> today
    - 'w' -> this week
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

    # Default to today
    if not timeframe or timeframe == 't':
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

        # Determine timeframe unit for header
        timeframe_str = args.timeframe if hasattr(args, 'timeframe') and args.timeframe else 't'
        unit = timeframe_str[0] if timeframe_str else 't'

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
        else:
            # Day view - just show the day
            print(colorize_timestamp(start_dt.strftime('%a %b %d, %Y')))

        # Print entries grouped by day
        for date_key in sorted_dates:
            entries = entries_by_date[date_key]
            # Sort entries within day by time
            entries.sort(key=lambda x: x[0])

            # Print day header (except for single day view)
            if unit != 't':
                day_name = entries[0][1].strftime('%a %b %d')
                print(f"  {colorize_timestamp(day_name)}")

            # Print entries
            for event_ts, event_dt, entry in entries:
                # Format entry with links
                display_text = entry.content
                if entry.data and 'links' in entry.data:
                    links = entry.data['links']
                    if links:
                        # Show links in [title](url) format
                        link_strs = []
                        for link in links:
                            if link.get('title'):
                                link_strs.append(f"[{link['title']}]({link['url']})")
                            else:
                                link_strs.append(link['url'])
                        display_text = f"{display_text} {' '.join(link_strs)}"

                # Handle multi-line content
                lines = display_text.split('\n')

                # Show time if not midnight
                if event_dt.hour != 0 or event_dt.minute != 0:
                    time_str = colorize_timestamp(event_dt.strftime('%H:%M'))
                    if unit == 't':
                        first_indent = "  "
                        subsequent_indent = "    "
                    else:
                        first_indent = "    "
                        subsequent_indent = "      "

                    # Print first line with time
                    print(f"{first_indent}{time_str} {lines[0]}")
                    # Print subsequent lines with extra indent
                    for line in lines[1:]:
                        print(f"{subsequent_indent}{line}")
                else:
                    # Midnight = all-day event, no time shown
                    if unit == 't':
                        first_indent = "  "
                        subsequent_indent = "    "
                    else:
                        first_indent = "    "
                        subsequent_indent = "      "

                    # Print first line
                    print(f"{first_indent}{lines[0]}")
                    # Print subsequent lines with extra indent
                    for line in lines[1:]:
                        print(f"{subsequent_indent}{line}")

    except Exception as e:
        print(f"Calendar view error: {e}", file=sys.stderr)
