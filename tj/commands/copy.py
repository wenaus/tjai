"""Copy command for tj entries."""

import sys
import uuid
from datetime import datetime, date, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import get_current_timezone


def handle_copy(args) -> None:
    """Handle copying an entry with new date/time.

    Usage:
    - tj cp <n> <date> - copy journal entry, preserve original time
    - tj cp <n> <date> <time> - copy journal entry with new time
    - tj cp <n> <date/time> - copy journal entry with new date and time

    Supports same date/time formats as tj j command.
    Entry type inferred from entry (currently only journal/calendar entries).
    """
    if not hasattr(args, 'args') or not args.args:
        print("Error: Usage: tj cp <entry_num> <datetime>", file=sys.stderr)
        return

    # Parse arguments
    if len(args.args) < 2:
        print("Error: Usage: tj cp <entry_num> <datetime>", file=sys.stderr)
        return

    try:
        entry_num = int(args.args[0])
    except ValueError:
        print(f"Error: Invalid entry number '{args.args[0]}'", file=sys.stderr)
        return

    # Get original entry
    entry = get_entry_from_recent_list(entry_num)
    if not entry:
        print(f"Error: Entry {entry_num} not found.", file=sys.stderr)
        return

    if entry.kind != 'calendar':
        print(f"Error: Entry {entry_num} is not a journal/calendar entry", file=sys.stderr)
        return

    # Get original event date/time
    if not entry.data or 'event_date' not in entry.data:
        print(f"Error: Entry {entry_num} has no event date", file=sys.stderr)
        return

    original_event_ts = entry.data['event_date']

    # Get timezone
    tz_name = get_current_timezone()
    try:
        tz = ZoneInfo(tz_name)
        original_dt = datetime.fromtimestamp(original_event_ts, tz=tz)
    except Exception:
        original_dt = datetime.fromtimestamp(original_event_ts)

    # Parse new date/time from remaining args
    date_time_args = args.args[1:]

    # Use journal parser to parse the date/time
    from tj.commands.journal import parse_date_spec
    new_event_ts, _ = parse_date_spec(date_time_args)

    if tz:
        new_dt = datetime.fromtimestamp(new_event_ts, tz=tz)
    else:
        new_dt = datetime.fromtimestamp(new_event_ts)

    # Check if time was specified in the new date/time
    # If the new datetime has midnight (00:00) but original had a time, preserve original time
    original_had_time = not (original_dt.hour == 0 and original_dt.minute == 0)
    new_is_midnight = (new_dt.hour == 0 and new_dt.minute == 0)

    # Check if user explicitly provided time by seeing if any arg contains ':' or 'am'/'pm'
    explicit_time = any(':' in arg or arg.lower().endswith(('am', 'pm')) for arg in date_time_args)

    # If no explicit time was given and original had a time, use original's time
    if not explicit_time and original_had_time:
        # Combine new date with original time
        if tz:
            new_dt = datetime.combine(new_dt.date(), time(original_dt.hour, original_dt.minute, tzinfo=tz))
        else:
            new_dt = datetime.combine(new_dt.date(), time(original_dt.hour, original_dt.minute))
        new_event_ts = new_dt.timestamp()

    # Create new entry by copying the old one
    now_ts = datetime.now(dt_timezone.utc).timestamp()

    # Copy entry data with new event_date
    new_data = dict(entry.data) if entry.data else {}
    new_data['event_date'] = new_event_ts

    repository = RepositoryFactory.get_repository()

    # Generate new ID
    new_id = str(uuid.uuid4())

    # Create the new entry
    from tj.repository import Entry
    new_entry = Entry(
        id=new_id,
        kind='calendar',
        content=entry.content,
        context=entry.context,
        timestamp_created=now_ts,
        timestamp_modified=now_ts,
        is_dirty=True,
        name=None,  # Don't copy name
        priority=entry.priority,
        status=entry.status,
        data=new_data
    )

    repository.create_entry(new_entry)

    # Copy tags
    tags = repository.get_tags(entry.id)
    for tag in tags:
        repository.add_tag(new_id, tag)

    # Format new datetime for display
    if new_dt.hour == 0 and new_dt.minute == 0:
        new_time_str = new_dt.strftime('%a %m/%d')
    else:
        new_time_str = new_dt.strftime('%a %m/%d %H:%M')

    print(f"Entry copied to {new_time_str}")
