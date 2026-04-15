"""Calendar view command for tj."""

import sys
import re
import traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple

from tj.colors import colorize_timestamp, colorize_content
from tj.repository_factory import RepositoryFactory
from tj.commands.common import format_entry_for_display
from tj.timezone_manager import get_current_timezone, get_timezone_object


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
    tz = get_timezone_object()
    now = datetime.now(tz) if tz else datetime.now()

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
        tz = get_timezone_object()

        # Query journal entries (for calendar presentation, apply safe mode filtering)
        from tj.commands.common import get_safe_exclude_tags
        all_calendar = repository.query_entries(kind='journal', exclude_tags=get_safe_exclude_tags())

        # Filter by event_date within timeframe and group by date (skip annual, handled below)
        entries_by_date = {}
        today_str = (datetime.now(tz) if tz else datetime.now()).strftime('%Y%m%d')
        for entry in all_calendar:
            if entry.mmdd is not None:
                continue  # Annual entries injected separately
            if entry.data and 'event_date' in entry.data:
                event_ts = entry.data['event_date']
                if start_ts <= event_ts < end_ts:
                    # Get date for grouping
                    if tz:
                        event_dt = datetime.fromtimestamp(event_ts, tz=tz)
                    else:
                        event_dt = datetime.fromtimestamp(event_ts)
                    date_key = event_dt.strftime('%Y%m%d')

                    # Daily synopsis only shows for today (matches dashboard)
                    eid = entry.data.get('entry_id', '')
                    if eid.startswith('daily-') and date_key != today_str:
                        continue

                    if date_key not in entries_by_date:
                        entries_by_date[date_key] = []
                    entries_by_date[date_key].append((event_ts, event_dt, entry))

        # Inject annual events
        today_tz = datetime.now(tz) if tz else datetime.now()
        today_mmdd = today_tz.month * 100 + today_tz.day
        annual_entries = repository.query_entries(kind='journal', tag='annual')
        seen_ids = set()
        for date_entries in entries_by_date.values():
            for _, _, entry in date_entries:
                seen_ids.add(entry.id)
        for entry in annual_entries:
            if entry.id in seen_ids or entry.mmdd is None:
                continue
            # Priority display rules: p=1 shows in full range, p=2+/None only today
            if entry.priority != 1 and entry.mmdd != today_mmdd:
                continue
            annual_month = entry.mmdd // 100
            annual_day = entry.mmdd % 100
            try:
                if tz:
                    projected_dt = today_tz.replace(month=annual_month, day=annual_day, hour=0, minute=0, second=0, microsecond=0)
                else:
                    projected_dt = datetime.now().replace(month=annual_month, day=annual_day, hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                continue
            projected_ts = projected_dt.timestamp()
            if not (start_ts <= projected_ts < end_ts):
                continue
            date_key = projected_dt.strftime('%Y%m%d')
            if date_key not in entries_by_date:
                entries_by_date[date_key] = []
            # Mark annual entries with a data flag for styling
            if entry.data is None:
                entry.data = {}
            entry.data['annual'] = True
            entries_by_date[date_key].append((projected_ts, projected_dt, entry))

        # Ensure today is always shown if it's in the timeframe
        if tz:
            now_dt = datetime.now(tz)
        else:
            now_dt = datetime.now()
        today_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        today_ts = today_dt.timestamp()
        today_key = today_dt.strftime('%Y%m%d')

        if start_ts <= today_ts < end_ts and today_key not in entries_by_date:
            entries_by_date[today_key] = []

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
        # Note: week headers are printed by the main loop, not here
        if unit == 'w':
            # For multi-week view, show date range
            end_dt = datetime.fromtimestamp(end_ts, tz=tz) if tz else datetime.fromtimestamp(end_ts)
            if (end_ts - start_ts) > (7 * 24 * 60 * 60):  # More than one week
                print(colorize_timestamp(f"{start_dt.strftime('%Y%m%d')}-{end_dt.strftime('%Y%m%d')} {desc}"))
            # Single week: no header here, loop prints week header
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

        # Find next upcoming event for countdown (today only)
        next_upcoming_ts = None
        if tz:
            now_dt = datetime.now(tz)
            today = now_dt.date()
        else:
            now_dt = datetime.now()
            today = now_dt.date()

        # Also track event currently in progress (started within last 30 min)
        in_progress_ts = None

        for date_key in sorted_dates:
            entries = entries_by_date[date_key]
            # Sort entries by time first
            entries.sort(key=lambda x: (0 if (x[1].hour == 0 and x[1].minute == 0) or (x[2].data or {}).get('entry_id', '').startswith('daily-') else 1, x[0]))
            event_date = entries[0][1].date() if entries else None
            if event_date == today:
                # Find earliest future event today, or event in progress
                # Skip clock entries and daily synopsis for NOW/countdown logic
                for event_ts, event_dt, entry in entries:
                    if entry.data and entry.data.get('clock'):
                        continue  # Skip clock entries
                    eid = entry.data.get('entry_id', '') if entry.data else ''
                    if eid.startswith('daily-'):
                        continue  # Skip daily synopsis
                    if event_dt.hour != 0 or event_dt.minute != 0:  # Not all-day
                        time_diff = event_dt - now_dt
                        seconds = time_diff.total_seconds()
                        if seconds > 0:  # In future
                            next_upcoming_ts = event_ts
                            break
                        elif seconds >= -1800:  # Started within last 30 min
                            in_progress_ts = event_ts
                break  # Only check today

        # Build flat list of all entry IDs in display order for numbered operations
        all_entry_ids = []
        for date_key in sorted_dates:
            entries = entries_by_date[date_key]
            entries.sort(key=lambda x: (0 if (x[1].hour == 0 and x[1].minute == 0) or (x[2].data or {}).get('entry_id', '').startswith('daily-') else 1, x[0]))
            for event_ts, event_dt, entry in entries:
                all_entry_ids.append(entry.id)

        # Save to state for numbered operations
        from tj.state import get_state, save_state
        state = get_state()
        state["last_query_results"] = all_entry_ids
        save_state(state)

        # Display entries with numbering
        entry_number = 1
        last_week = None
        for date_key in sorted_dates:
            entries = entries_by_date[date_key]
            # Sort entries within day by time
            entries.sort(key=lambda x: (0 if (x[1].hour == 0 and x[1].minute == 0) or (x[2].data or {}).get('entry_id', '').startswith('daily-') else 1, x[0]))

            # Get date from entry or from date_key if no entries
            if entries:
                event_date = entries[0][1].date()
                event_dt_for_display = entries[0][1]
            else:
                # Parse date_key to get date
                parsed_dt = datetime.strptime(date_key, '%Y%m%d')
                if tz:
                    parsed_dt = parsed_dt.replace(tzinfo=tz)
                event_date = parsed_dt.date()
                event_dt_for_display = parsed_dt

            # Determine if this is today's section
            if tz:
                now_dt = datetime.now(tz)
                today = now_dt.date()
            else:
                now_dt = datetime.now()
                today = now_dt.date()
            is_today = (event_date == today)

            # Check for week boundary in multi-day views
            if is_multi_day:
                current_week = event_dt_for_display.isocalendar()[1]
                current_year = event_dt_for_display.year

                # Print week header when crossing week boundary or at start
                if last_week is None or (current_year, current_week) != last_week:
                    week_start = event_dt_for_display - timedelta(days=event_dt_for_display.weekday())
                    week_header = f'{week_start.strftime("%Y%m%d")} Week {current_week}'
                    print(f"\n{colorize_timestamp(week_header)}")
                    last_week = (current_year, current_week)

            # Print day header for multi-day views
            if is_multi_day:
                day_name = event_dt_for_display.strftime('%a %b %-d')
                if is_today:
                    from tj.colors import BOLD, RESET, RED
                    current_time = now_dt.strftime('%H:%M')
                    print(f"  {BOLD}{colorize_timestamp(f'{day_name} {current_time}')} {RED}{BOLD}Today{RESET}")
                else:
                    print(f"  {colorize_timestamp(day_name)}")

            # Print entries
            for event_ts, event_dt, entry in entries:
                # Daily briefings: single clickable line linking to synopsis
                entry_id = entry.data.get('entry_id', '') if entry.data else ''
                if entry_id.startswith('daily-'):
                    from tj.server import DEFAULT_SERVER
                    from tj.colors import BRIGHT_CYAN_BLUE, RESET
                    title = entry.content.split('\n')[0]
                    url = f"{DEFAULT_SERVER}/synopsis/?entry_id={entry_id}"
                    content = f"{BRIGHT_CYAN_BLUE}\x1B]8;;{url}\x1B\\{title}\x1B]8;;\x1B\\{RESET}"
                else:
                    # Truncate content if needed (by lines, before colorizing)
                    # First line always shown + truncate_len additional lines
                    content = entry.content
                    lines = content.split('\n')
                    if len(lines) > (truncate_len + 1):
                        content = '\n'.join(lines[:truncate_len + 1]) + " [...]"

                # Check if this is a clock entry for special coloring
                clock_type = entry.data.get('clock') if entry.data else None

                # Build context prefix for clock entries
                context_prefix = ""
                if clock_type and entry.context:
                    from tj.colors import colorize_context
                    context_prefix = f"{colorize_context(entry.context)} "

                # Apply clock colors to title only, description in normal color
                if clock_type:
                    from tj.colors import LIGHT_MINT_GREEN, DARKER_GREEN, RESET
                    # Split into title (first line) and description (rest), strip leading blank lines
                    content_lines = content.split('\n')
                    while content_lines and not content_lines[0].strip():
                        content_lines.pop(0)
                    title = content_lines[0] if content_lines else content
                    desc_lines = content_lines[1:] if len(content_lines) > 1 else []
                    # Strip leading blank lines from description too
                    while desc_lines and not desc_lines[0].strip():
                        desc_lines.pop(0)

                    clock_color = LIGHT_MINT_GREEN if clock_type == 'start' else DARKER_GREEN
                    if desc_lines:
                        # Title in clock color, description in normal (colorized for links)
                        desc_text = colorize_content('\n'.join(desc_lines))
                        display_text = f"{clock_color}{title}{RESET}\n{desc_text}"
                    else:
                        display_text = f"{clock_color}{title}{RESET}"
                elif entry_id.startswith('daily-'):
                    # Already formatted as hyperlink above, no further colorization
                    display_text = content
                elif entry.data and entry.data.get('annual'):
                    from tj.colors import LIGHT_PINK_PURPLE, RESET
                    display_text = f"{LIGHT_PINK_PURPLE}{content}{RESET}"
                else:
                    # Colorize content (converts markdown links to clickable terminal links)
                    display_text = colorize_content(content)

                # Calculate countdown for next upcoming event, or NOW for in-progress
                countdown_str = ""
                arrow_suffix = ""
                if event_ts == in_progress_ts:
                    from tj.colors import RED, RESET, BOLD
                    countdown_str = f" {RED}{BOLD}NOW{RESET}"
                    arrow_suffix = f"     {RED}{BOLD}<======== NOW{RESET}"
                elif event_ts == next_upcoming_ts:
                    time_diff = event_dt - now_dt
                    total_seconds = int(time_diff.total_seconds())
                    from tj.colors import RED, RESET, BOLD
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    if hours == 0:  # Less than 1 hour - show arrows at end
                        countdown_str = f" {RED}{BOLD}in {minutes}m{RESET}"
                        arrow_suffix = f"     {RED}{BOLD}<======== in {minutes}m{RESET}"
                    else:
                        countdown_str = f" {RED}{BOLD}in {hours}h {minutes}m{RESET}"

                # Handle multi-line content
                lines = display_text.split('\n')

                # Show time if not all-day (midnight or daily- entries)
                is_allday = entry_id.startswith('daily-') or (event_dt.hour == 0 and event_dt.minute == 0)
                if not is_allday:
                    time_str = colorize_timestamp(event_dt.strftime('%H:%M'))

                    # Add grey entry number after indent, 1 space before time
                    from tj.colors import SOFT_GREY, RESET as COLOR_RESET
                    number_str = f"{SOFT_GREY}{entry_number:2d}{COLOR_RESET}"

                    if is_multi_day:
                        first_indent = "    "
                        subsequent_indent = "      "
                    else:
                        first_indent = "  "
                        subsequent_indent = "    "

                    # Build first line with optional bold for today and countdown
                    if is_today:
                        from tj.colors import BOLD, RESET
                        first_line = f"{first_indent}{number_str} {BOLD}{time_str}{countdown_str} {context_prefix}{lines[0]}{arrow_suffix}{RESET}"
                    else:
                        first_line = f"{first_indent}{number_str} {time_str} {context_prefix}{lines[0]}"

                    print(first_line)
                    entry_number += 1
                    # Print subsequent lines with extra indent and bold if today
                    for line in lines[1:]:
                        if is_today:
                            from tj.colors import BOLD, RESET
                            print(f"{subsequent_indent}{BOLD}{line}{RESET}")
                        else:
                            print(f"{subsequent_indent}{line}")
                else:
                    # Midnight = all-day event, no time shown
                    # Add grey entry number after indent, 1 space before content
                    from tj.colors import SOFT_GREY, RESET as COLOR_RESET
                    number_str = f"{SOFT_GREY}{entry_number:2d}{COLOR_RESET}"

                    if is_multi_day:
                        first_indent = "    "
                        subsequent_indent = "      "
                    else:
                        first_indent = "  "
                        subsequent_indent = "    "

                    # Print with bold if today
                    if is_today:
                        from tj.colors import BOLD, RESET
                        print(f"{first_indent}{number_str} {BOLD}{context_prefix}{lines[0]}{RESET}")
                        for line in lines[1:]:
                            print(f"{subsequent_indent}{BOLD}{line}{RESET}")
                    else:
                        print(f"{first_indent}{number_str} {context_prefix}{lines[0]}")
                        for line in lines[1:]:
                            print(f"{subsequent_indent}{line}")
                    entry_number += 1

    except Exception as e:
        traceback.print_exc()
        print(f"Calendar view error: {e}", file=sys.stderr)


def handle_yearly_summary(args) -> None:
    """Display a 1-year summary with month and week headers showing event counts."""
    try:
        repository = RepositoryFactory.get_repository()

        # Get timezone
        tz = get_timezone_object()
        now = datetime.now(tz) if tz else datetime.now()

        # Calculate 1-year range from today
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=365)
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()

        # Query all journal entries
        from tj.commands.common import get_safe_exclude_tags
        all_calendar = repository.query_entries(kind='journal', exclude_tags=get_safe_exclude_tags())

        # Group events by year-month and year-week
        events_by_month = {}  # 'YYYY-MM' -> count
        events_by_week = {}   # 'YYYY-WW' -> count

        for entry in all_calendar:
            if entry.data and 'event_date' in entry.data:
                event_ts = entry.data['event_date']
                if start_ts <= event_ts < end_ts:
                    if tz:
                        event_dt = datetime.fromtimestamp(event_ts, tz=tz)
                    else:
                        event_dt = datetime.fromtimestamp(event_ts)

                    month_key = event_dt.strftime('%Y-%m')
                    year, week_num, _ = event_dt.isocalendar()
                    week_key = f"{year}-{week_num:02d}"

                    events_by_month[month_key] = events_by_month.get(month_key, 0) + 1
                    events_by_week[week_key] = events_by_week.get(week_key, 0) + 1

        # Print header
        print(colorize_timestamp(f"Year summary: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}"))

        # Collect events by month for showing details when < 5
        events_by_month_list = {}  # 'YYYY-MM' -> list of (event_ts, entry)
        for entry in all_calendar:
            if entry.data and 'event_date' in entry.data:
                event_ts = entry.data['event_date']
                if start_ts <= event_ts < end_ts:
                    if tz:
                        event_dt = datetime.fromtimestamp(event_ts, tz=tz)
                    else:
                        event_dt = datetime.fromtimestamp(event_ts)
                    month_key = event_dt.strftime('%Y-%m')
                    if month_key not in events_by_month_list:
                        events_by_month_list[month_key] = []
                    events_by_month_list[month_key].append((event_ts, entry))

        # Iterate through each month in the year
        current_dt = start_dt
        while current_dt < end_dt:
            month_key = current_dt.strftime('%Y-%m')
            month_count = events_by_month.get(month_key, 0)
            month_name = current_dt.strftime('%B %Y')

            # Month header with count (omit count if zero)
            if month_count > 0:
                print(f"{colorize_timestamp(month_name)} - {month_count}")
            else:
                print(f"{colorize_timestamp(month_name)}")

            # Show all events using same display as tj c
            if month_count > 0:
                month_events = sorted(events_by_month_list.get(month_key, []), key=lambda x: x[0])
                for event_ts, entry in month_events:
                    if tz:
                        event_dt = datetime.fromtimestamp(event_ts, tz=tz)
                    else:
                        event_dt = datetime.fromtimestamp(event_ts)
                    weekday_str = event_dt.strftime('%a')
                    date_str = event_dt.strftime('%m/%d')
                    time_str = ""
                    if event_dt.hour != 0 or event_dt.minute != 0:
                        time_str = event_dt.strftime('%H:%M') + " "
                    content = entry.content.split('\n')[0]
                    display_text = colorize_content(content)
                    print(f"  {weekday_str} {date_str} {time_str}{display_text}")

            # Move to next month
            if current_dt.month == 12:
                current_dt = current_dt.replace(year=current_dt.year + 1, month=1, day=1)
            else:
                current_dt = current_dt.replace(month=current_dt.month + 1, day=1)

    except Exception as e:
        traceback.print_exc()
        print(f"Yearly summary error: {e}", file=sys.stderr)
