"""Clock commands for time tracking."""

import sys
import uuid
from datetime import datetime, timezone, date, time as dt_time
from typing import Optional, List, Dict, Any

from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.repository_sqlite import encode_entry_data, decode_entry_data
from tj.state import get_state
from tj.timezone_manager import get_timezone_object, format_time_only
from tj.commands.journal import parse_time


def get_active_clock_for_context(context: Optional[str]) -> Optional[Dict[str, Any]]:
    """Get active (running) clock for specific context, or None.

    Fast lookup via latest_clock_start_id.
    """
    from tj.database import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM sync_metadata WHERE key = 'latest_clock_start_id'")
    row = cursor.fetchone()
    if not row:
        return None

    latest_id = row['value']

    repository = RepositoryFactory.get_repository()
    entry = repository.get_entry(latest_id)
    if not entry or not entry.data:
        return None

    try:
        data = decode_entry_data(entry.data)
    except ValueError as e:
        print(f"Warning: Corrupt clock data in entry {latest_id}: {e}", file=sys.stderr)
        return None

    # Must be a start entry, running (no stop_id), and matching context
    if data.get('clock') != 'start':
        return None
    if data.get('stop_id'):
        return None
    if entry.context != context:
        return None

    return {
        'entry': entry,
        'data': data,
        'context': entry.context
    }


def format_duration(minutes: int) -> str:
    """Format duration in minutes to human readable string."""
    hours = minutes // 60
    mins = minutes % 60

    if hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"


def parse_clock_time(time_str: Optional[str]) -> float:
    """Parse optional time string, return timestamp.

    If time_str is None, returns current time.
    Otherwise parses time and returns timestamp for today at that time.
    """
    tz = get_timezone_object()

    if tz:
        now = datetime.now(tz)
    else:
        now = datetime.now()

    if not time_str:
        return now.timestamp()

    try:
        hour, minute = parse_time(time_str)
        today = now.date()

        if tz:
            dt = datetime.combine(today, dt_time(hour, minute, tzinfo=tz))
        else:
            dt = datetime.combine(today, dt_time(hour, minute))

        return dt.timestamp()
    except ValueError as e:
        print(f"Error: Invalid time format '{time_str}': {e}", file=sys.stderr)
        sys.exit(1)


def handle_clock_start(args) -> None:
    """Start time clock in current context."""
    state = get_state()
    context = state.get("current_context")

    # Check if already clocked in for this context
    active = get_active_clock_for_context(context)
    if active:
        entry = active['entry']
        start_time = entry.data.get('event_date', entry.timestamp_created) if isinstance(entry.data, dict) else entry.timestamp_created
        try:
            data = decode_entry_data(entry.data)
            if data:
                start_time = data.get('event_date', entry.timestamp_created)
        except ValueError as e:
            print(f"Warning: Failed to parse clock data: {e}", file=sys.stderr)
            start_time = entry.timestamp_created

        ctx_display = f"={context}" if context else "(no context)"
        print(f"Error: Clock already running for {ctx_display}", file=sys.stderr)
        return

    # Parse time
    time_arg = args.time if hasattr(args, 'time') else None
    start_timestamp = parse_clock_time(time_arg)

    # Create journal entry with clock data
    repository = RepositoryFactory.get_repository()
    entry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).timestamp()

    entry = Entry(
        id=entry_id,
        content="▶ Clock start",
        kind='journal',
        timestamp_created=now,
        timestamp_modified=now,
        context=context,
        is_dirty=True,
        data=encode_entry_data(clock='start', event_date=start_timestamp, breaks=0, stop_id=None)
    )

    repository.create_entry(entry)

    # Store latest clock start ID for fast lookup
    from tj.database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated) VALUES ('latest_clock_start_id', ?, ?)",
        (entry_id, now)
    )
    conn.commit()

    # Format output
    tz = get_timezone_object()
    time_display = format_time_only(start_timestamp, tz.key if tz else None)
    ctx_display = f"={context}" if context else "(no context)"
    print(f"Clock started at {time_display} for {ctx_display}")


def handle_clock_stop(args) -> None:
    """Stop time clock."""
    state = get_state()
    context = state.get("current_context")

    # Find active clock for this context
    active = get_active_clock_for_context(context)
    if not active:
        ctx_display = f"={context}" if context else "(no context)"
        print(f"Error: No clock running for {ctx_display}", file=sys.stderr)
        return

    start_entry = active['entry']
    start_data = active['data']

    # Parse stop time
    time_arg = args.time if hasattr(args, 'time') else None
    stop_timestamp = parse_clock_time(time_arg)

    # Get start time from data
    start_timestamp = start_data.get('event_date', start_entry.timestamp_created)
    break_min = start_data.get('breaks', 0)

    # Calculate durations in minutes
    elapsed_min = int((stop_timestamp - start_timestamp) / 60)
    work_min = max(0, elapsed_min - break_min)

    # Create stop entry
    repository = RepositoryFactory.get_repository()
    stop_entry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).timestamp()

    stop_entry = Entry(
        id=stop_entry_id,
        content=f"■ Clock stop ({format_duration(work_min)} work, {format_duration(break_min)} breaks)",
        kind='journal',
        timestamp_created=now,
        timestamp_modified=now,
        context=context,
        is_dirty=True,
        data=encode_entry_data(clock='stop', event_date=stop_timestamp, start_id=start_entry.id)
    )

    repository.create_entry(stop_entry)

    # Update start entry with stop_id
    start_data['stop_id'] = stop_entry_id
    repository.update_entry(start_entry.id, data=start_data, timestamp_modified=now, is_dirty=True)

    # Format output
    tz = get_timezone_object()
    time_display = format_time_only(stop_timestamp, tz.key if tz else None)

    print(f"Clock stopped at {time_display}.")
    print(f"Session: {format_duration(elapsed_min)} elapsed, {format_duration(work_min)} work, {format_duration(break_min)} breaks.")


def handle_clock_break(args) -> None:
    """Add break time to current clock session."""
    state = get_state()
    context = state.get("current_context")

    # Find active clock for this context
    active = get_active_clock_for_context(context)
    if not active:
        ctx_display = f"={context}" if context else "(no context)"
        print(f"Error: No clock running for {ctx_display}", file=sys.stderr)
        return

    # Parse duration
    duration_str = args.duration
    if duration_str.lower().endswith('h'):
        # Hours
        try:
            hours = float(duration_str[:-1])
            minutes = int(hours * 60)
        except ValueError:
            print(f"Error: Invalid duration '{duration_str}'", file=sys.stderr)
            return
    else:
        # Minutes (default)
        try:
            minutes = int(duration_str)
        except ValueError:
            print(f"Error: Invalid duration '{duration_str}'. Use number for minutes or Nh for hours.", file=sys.stderr)
            return

    # Update start entry with additional break time
    start_entry = active['entry']
    start_data = active['data']

    current_breaks = start_data.get('breaks', 0)
    new_breaks = current_breaks + minutes
    start_data['breaks'] = new_breaks

    repository = RepositoryFactory.get_repository()
    now = datetime.now(timezone.utc).timestamp()

    repository.update_entry(start_entry.id, data=start_data, timestamp_modified=now, is_dirty=True)

    print(f"Added {minutes}m break. Total breaks: {format_duration(new_breaks)}.")


def get_clock_status_for_display() -> List[str]:
    """Get clock status line for display in header.

    Fast lookup via latest_clock_start_id in sync_metadata.
    Shows current session whether running or stopped.
    """
    from tj.colors import LIGHT_MINT_GREEN, RESET
    from tj.database import get_db_connection

    # Get latest clock start ID
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM sync_metadata WHERE key = 'latest_clock_start_id'")
    row = cursor.fetchone()
    if not row:
        return []

    latest_id = row['value']

    # Fetch the start entry
    repository = RepositoryFactory.get_repository()
    entry = repository.get_entry(latest_id)
    if not entry or not entry.data:
        return []

    try:
        data = decode_entry_data(entry.data)
    except ValueError:
        return []

    if data.get('clock') != 'start':
        return []

    tz = get_timezone_object()
    if tz:
        now = datetime.now(tz)
    else:
        now = datetime.now()

    ctx = entry.context
    ctx_display = f"={ctx}" if ctx else "(no context)"
    start_time = data.get('event_date', entry.timestamp_created)
    breaks_min = data.get('breaks', 0)
    stop_id = data.get('stop_id')

    if stop_id:
        # Stopped session - no active clock, return nothing
        return []

    # Running session
    end_time = now.timestamp()

    elapsed_min = int((end_time - start_time) / 60)
    work_min = max(0, elapsed_min - breaks_min)

    elapsed = format_duration(elapsed_min)
    work = format_duration(work_min)
    breaks = format_duration(breaks_min)

    line = f"{LIGHT_MINT_GREEN}Clocked in {ctx_display}: {elapsed} elapsed, {work} work, {breaks} breaks.{RESET}"
    return [line]
