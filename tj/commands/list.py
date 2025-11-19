"""List command handlers for tj."""

import sys
from datetime import datetime, timedelta

from tj.colors import colorize_content, colorize_context, colorize_kind, colorize_timestamp, colorize_creation_timestamp, colorize_entry_number
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state, display_context
from tj.timezone_manager import format_time_dashboard


def handle_list_command(args) -> None:
    """Handle the list command with optional filters.

    Supports:
    - tj l - all entries
    - tj l c - list contexts (metadata)
    - tj l t - list tags (metadata)
    - tj l d - list todos
    - tj l =context - entries in context
    - tj l :tag - entries with tag
    - tj l p=1 s=active - composite filters
    - tj l t/w/m - time filters
    """
    display_context()
    try:
        repository = RepositoryFactory.get_repository()
        filters = args.filters if hasattr(args, 'filters') and args.filters else []

        # Check for metadata commands first (c=contexts, t=tags, @=named entries)
        if len(filters) == 1:
            if filters[0] == 'c':
                _list_contexts(repository)
                return
            elif filters[0] == 't':
                _list_tags(repository)
                return
            elif filters[0] == '@':
                _list_named_entries(repository)
                return

        # Otherwise, list entries with filters
        _list_entries_with_filters(repository, filters)

    except Exception as e:
        print(f"List error: {e}", file=sys.stderr)


def _list_contexts(repository):
    """List all contexts."""
    context_entities = repository.get_all_contexts()

    # Get current context
    state = get_state()
    current_context = state.get("current_context")

    if current_context:
        print(f"Current context: {current_context}")
    else:
        print("Current context: none")

    if not context_entities:
        print("No contexts found.")
        return

    print(f"Contexts ({len(context_entities)}):")
    for i, context in enumerate(sorted(context_entities, key=lambda c: c.name), 1):
        # Count entries in this context
        entries = repository.query_entries(context=context.name)
        active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

        # Colorize current context (colorize_context adds = prefix, just use the color)
        if context.name == current_context:
            from tj.colors import LIGHT_MAUVE, RESET
            context_name = f"{LIGHT_MAUVE}{context.name}{RESET}"
        else:
            context_name = context.name

        # Build display parts: name - title - description - N entries
        display_parts = [f"{i:2d}  {context_name}"]
        if context.title:
            display_parts.append(context.title)
        if context.description:
            display_parts.append(context.description)
        display_parts.append(f"{len(active_entries)} entries")
        print(" - ".join(display_parts))


def _list_named_entries(repository):
    """List all named entries."""
    all_entries = repository.query_entries()
    named_entries = [e for e in all_entries if e.name and not getattr(e, 'deleted_at', None)]

    if not named_entries:
        print("No named entries found.")
        return

    # Sort by modification time, oldest first (newest at bottom)
    named_entries.sort(key=lambda e: e.timestamp_modified, reverse=False)

    print(f"Named entries ({len(named_entries)}):")
    for i, entry in enumerate(named_entries, 1):
        time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_modified))
        content_preview = entry.content[:60] + "..." if len(entry.content) > 60 else entry.content
        context_str = f" {colorize_context(entry.context)}" if entry.context else ""
        print(f"{colorize_entry_number(i)}  @{entry.name} {time_str}{context_str} {content_preview}")

    # Store numbered entries in state for numbered operations
    state = get_state()
    state["last_query_results"] = [e.id for e in named_entries]
    state["last_query_time"] = datetime.now().timestamp()
    save_state(state)


def _list_tags(repository):
    """List all tags."""
    all_tags = repository.get_all_tags()
    if not all_tags:
        print("No tags found.")
        return

    # Count usage per tag
    tag_counts = {}
    for tag in all_tags:
        tag_counts[tag.tag_name] = tag_counts.get(tag.tag_name, 0) + 1

    print(f"{len(tag_counts)} tags:")
    for i, tag_name in enumerate(sorted(tag_counts.keys()), 1):
        count = tag_counts[tag_name]
        print(f"{colorize_entry_number(i)}  {tag_name} - {count}")


def _list_entries_with_filters(repository, filters):
    """List entries with composite filters."""
    # Build query parameters
    kind = None
    context = None
    tag = None
    priority = None
    status = None
    time_cutoff = None
    text_filter = None
    query_parts = []

    for filter_arg in filters:
        # Check for numeric day limit
        if filter_arg.isdigit():
            days_back = int(filter_arg)
            now = datetime.now()
            time_cutoff = (now - timedelta(days=days_back)).timestamp()
            query_parts.append(f"last {days_back} days")
            continue
        # Query by kind
        if filter_arg in ['b', 'd', 'p', 'ai', 'r', 'j']:
            kind_map = {
                'b': 'bookmark',
                'd': 'todo',
                'p': 'profile',
                'ai': 'ai',
                'r': 'memory',
                'j': 'calendar'
            }
            kind = kind_map[filter_arg]
            query_parts.append(kind)

        # Query by time
        elif filter_arg in ['t', 'w', 'm']:
            now = datetime.now()
            if filter_arg == 't':  # today
                start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                time_cutoff = start_of_day.timestamp()
                query_parts.append("today")
            elif filter_arg == 'w':  # week
                start_of_week = now - timedelta(days=7)
                time_cutoff = start_of_week.timestamp()
                query_parts.append("last 7 days")
            elif filter_arg == 'm':  # month
                start_of_month = now - timedelta(days=30)
                time_cutoff = start_of_month.timestamp()
                query_parts.append("last 30 days")

        # Query by context
        elif filter_arg.startswith('='):
            context = filter_arg[1:]
            if not context:
                print("Error: Empty context name.", file=sys.stderr)
                return
            query_parts.append(f"{filter_arg}")

        # Query by tag
        elif filter_arg.startswith(':'):
            tag = filter_arg[1:]
            if not tag:
                print("Error: Empty tag name.", file=sys.stderr)
                return
            query_parts.append(f"{filter_arg}")

        # Query by priority
        elif filter_arg.startswith('p='):
            try:
                priority = int(filter_arg[2:])
                query_parts.append(f"{filter_arg}")
            except ValueError:
                print(f"Error: Invalid priority value '{filter_arg}'", file=sys.stderr)
                return

        # Query by status
        elif filter_arg.startswith('s='):
            status = filter_arg[2:]
            if not status:
                print("Error: Empty status value.", file=sys.stderr)
                return
            query_parts.append(f"{filter_arg}")

        else:
            # Treat as text filter
            text_filter = filter_arg.lower()
            query_parts.append(f"text:{filter_arg}")

    # Execute query
    entries = repository.query_entries(
        kind=kind,
        context=context,
        tag=tag,
        priority=priority,
        status=status
    )

    # Apply time filter (post-query)
    if time_cutoff:
        entries = [e for e in entries if e.timestamp_created >= time_cutoff]

    # Apply text filter (post-query, case-insensitive)
    if text_filter:
        entries = [e for e in entries if text_filter in e.content.lower()]

    # Build query description
    if query_parts:
        # Check if it's just a kind filter for cleaner display
        if len(query_parts) == 1 and query_parts[0] in ['bookmark', 'todo', 'profile', 'ai', 'memory', 'calendar']:
            query_desc = f"{query_parts[0]}"
        else:
            query_desc = " ".join(query_parts)
    else:
        query_desc = "all"

    # Filter out deleted entries
    active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

    # Sort by timestamp, oldest first (newest at bottom)
    active_entries.sort(key=lambda e: e.timestamp_modified, reverse=False)

    # Display results
    if not active_entries:
        print(f"No {query_desc} entries found")
        return

    # Capitalize first letter of query_desc for display
    display_desc = query_desc.capitalize() if query_desc else "All"
    print(f"{display_desc} entries ({len(active_entries)}):")
    for i, entry in enumerate(active_entries, 1):
        # Format modification timestamp uniformly for all entries
        time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_modified))
        content_colored = colorize_content(entry.content)

        # Show type prefix for non-memory entries (use short codes)
        kind_display = {
            'todo': 'd',
            'profile': 'p',
            'ai': 'ai',
            'calendar': 'j',
            'bookmark': 'b'
        }
        if entry.kind in kind_display:
            type_prefix = f"{colorize_kind(kind_display[entry.kind])} "
        else:
            type_prefix = ""

        # For calendar entries, show event date/time after type, before context
        event_date_str = ""
        if entry.kind == 'calendar' and entry.data and 'event_date' in entry.data:
            from tj.timezone_manager import get_current_timezone
            from zoneinfo import ZoneInfo

            tz_name = get_current_timezone()
            try:
                tz = ZoneInfo(tz_name)
                event_dt = datetime.fromtimestamp(entry.data['event_date'], tz=tz)
            except Exception:
                event_dt = datetime.fromtimestamp(entry.data['event_date'])

            # If time is midnight (00:00), show just date with weekday
            if event_dt.hour == 0 and event_dt.minute == 0:
                event_date_str = f"{colorize_timestamp(event_dt.strftime('%a %m/%d'))} "
            else:
                # Show full date and time with weekday
                event_date_str = f"{colorize_timestamp(event_dt.strftime('%a %m/%d/%H:%M'))} "

        # Show context after event date
        context_str = f"{colorize_context(entry.context)} " if entry.context else ""

        print(f"{colorize_entry_number(i)}  {time_str} {type_prefix}{event_date_str}{context_str}{content_colored}")

    # Store numbered entries in state for numbered operations
    state = get_state()
    state["last_query_results"] = [e.id for e in active_entries]
    state["last_query_time"] = datetime.now().timestamp()
    save_state(state)


def handle_list_all(args) -> None:
    """Handle listing all entries with optional filter (text or day count)."""
    try:
        repository = RepositoryFactory.get_repository()

        # Get all active entries
        all_entries = repository.query_entries()
        active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]

        # Apply filter - could be day count or text filter
        if args.filter:
            if args.filter.isdigit():
                # Day count filter (tj a 3)
                days = int(args.filter)
                seconds_per_day = 24 * 60 * 60
                days_ago = datetime.now().timestamp() - (days * seconds_per_day)
                filtered_entries = [e for e in active_entries if e.timestamp_created >= days_ago]
            else:
                # Text filter (tj a test)
                filter_text = args.filter.lower()
                filtered_entries = [e for e in active_entries if filter_text in e.content.lower()]
        else:
            filtered_entries = active_entries

        if not filtered_entries:
            if args.filter:
                if args.filter.isdigit():
                    print(f"No entries found from last {args.filter} day(s).")
                else:
                    print(f"No entries found matching '{args.filter}'.")
            else:
                print("No entries found.")
            return

        # Sort by timestamp (newest first)
        sorted_entries = sorted(filtered_entries, key=lambda e: e.timestamp_modified, reverse=False)

        # Display results
        if args.filter:
            if args.filter.isdigit():
                filter_info = f" from last {args.filter} day(s)"
            else:
                filter_info = f" matching '{args.filter}'"
        else:
            filter_info = ""
        print(f"{len(sorted_entries)} entries{filter_info}:")

        for i, entry in enumerate(sorted_entries, 1):
            content_colored = colorize_content(entry.content)
            time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))

            context_str = f" {colorize_context(entry.context)}" if entry.context else ""
            if entry.kind == 'todo':
                print(f"{colorize_entry_number(i)}  {time_str} ToDo: {content_colored}{context_str}")
            elif entry.kind in ['memory', 'bookmark']:
                print(f"{colorize_entry_number(i)}  {time_str} {content_colored}{context_str}")
            else:
                print(f"{colorize_entry_number(i)}  {time_str} [{entry.kind}] {content_colored}{context_str}")

    except Exception as e:
        print(f"List all error: {e}", file=sys.stderr)
