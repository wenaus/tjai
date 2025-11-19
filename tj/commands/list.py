"""List command handlers for tj."""

import sys
from datetime import datetime, timedelta

from tj.colors import colorize_content, colorize_context, colorize_kind, colorize_timestamp
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state
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
    try:
        repository = RepositoryFactory.get_repository()
        filters = args.filters if hasattr(args, 'filters') and args.filters else []

        # Check for metadata commands first (c=contexts, t=tags)
        if len(filters) == 1:
            if filters[0] == 'c':
                _list_contexts(repository)
                return
            elif filters[0] == 't':
                _list_tags(repository)
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

    print(f"{len(context_entities)} contexts:")
    for i, context in enumerate(sorted(context_entities, key=lambda c: c.name), 1):
        # Count entries in this context
        entries = repository.query_entries(context=context.name)
        active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

        # Mark current context
        current_marker = " *" if context.name == current_context else ""

        # Show context with title and/or description
        display_parts = [f"{i:2d}  {context.name}", f"{len(active_entries)}"]
        if context.title:
            display_parts.insert(1, f'"{context.title}"')
        if context.description:
            display_parts.append(context.description)
        print(" - ".join(display_parts) + current_marker)


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
        print(f"{i:2d}  {tag_name} - {count}")


def _list_entries_with_filters(repository, filters):
    """List entries with composite filters."""
    # Build query parameters
    kind = None
    context = None
    tag = None
    priority = None
    status = None
    time_cutoff = None
    query_parts = []

    for filter_arg in filters:
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
            query_parts.append(f"kind={kind}")

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
            query_parts.append(f"context={context}")

        # Query by tag
        elif filter_arg.startswith(':'):
            tag = filter_arg[1:]
            if not tag:
                print("Error: Empty tag name.", file=sys.stderr)
                return
            query_parts.append(f"tag={tag}")

        # Query by priority
        elif filter_arg.startswith('p='):
            try:
                priority = int(filter_arg[2:])
                query_parts.append(f"priority={priority}")
            except ValueError:
                print(f"Error: Invalid priority value '{filter_arg}'", file=sys.stderr)
                return

        # Query by status
        elif filter_arg.startswith('s='):
            status = filter_arg[2:]
            if not status:
                print("Error: Empty status value.", file=sys.stderr)
                return
            query_parts.append(f"status={status}")

        else:
            print(f"Error: Unknown filter '{filter_arg}'", file=sys.stderr)
            print("Usage: tj l [c|t|d|p|b|j|ai|=ctx|:tag|p=N|s=val|t/w/m]", file=sys.stderr)
            return

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

    # Build query description
    query_desc = " AND ".join(query_parts) if query_parts else "all entries"

    # Filter out deleted entries
    active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

    # Sort by timestamp, newest first
    active_entries.sort(key=lambda e: e.timestamp_created, reverse=True)

    # Display results
    if not active_entries:
        print(f"No entries found for {query_desc}")
        return

    print(f"{len(active_entries)} entries for {query_desc}:")
    for i, entry in enumerate(active_entries, 1):
        # Format creation timestamp uniformly for all entries
        time_str = colorize_timestamp(format_time_dashboard(entry.timestamp_created))
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

        # Show context in square brackets after type
        context_str = f"{colorize_context(entry.context)} " if entry.context else ""

        # For calendar entries, show event date/time after metadata, before content
        event_date_str = ""
        if entry.kind == 'calendar' and entry.data and 'event_date' in entry.data:
            event_dt = datetime.fromtimestamp(entry.data['event_date'])
            # If time is midnight (00:00), show just date as YYYYMMDD
            if event_dt.hour == 0 and event_dt.minute == 0:
                event_date_str = f"{colorize_timestamp(event_dt.strftime('%Y%m%d'))} "
            else:
                # Show full date and time as YYYYMMDD/HH:MM
                event_date_str = f"{colorize_timestamp(event_dt.strftime('%Y%m%d/%H:%M'))} "

        print(f"{i:2d}  {time_str} {type_prefix}{context_str}{event_date_str}{content_colored}")

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
        sorted_entries = sorted(filtered_entries, key=lambda e: e.timestamp_created, reverse=True)

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
            time_str = colorize_timestamp(format_time_dashboard(entry.timestamp_created))

            context_str = f" {colorize_context(entry.context)}" if entry.context else ""
            if entry.kind == 'todo':
                print(f"{i:2d}  {time_str} ToDo: {content_colored}{context_str}")
            elif entry.kind in ['memory', 'bookmark']:
                print(f"{i:2d}  {time_str} {content_colored}{context_str}")
            else:
                print(f"{i:2d}  {time_str} [{entry.kind}] {content_colored}{context_str}")

    except Exception as e:
        print(f"List all error: {e}", file=sys.stderr)
