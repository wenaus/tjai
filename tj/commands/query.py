"""Query command for searching entries."""

import sys
from datetime import datetime, timedelta
from typing import Optional

from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state
from tj.commands.common import format_entry_for_display


def handle_query(args, num_identifier: Optional[int] = None) -> None:
    """Handle query commands.

    Supported queries:
    - tj q - all entries
    - tj q b/d/p/ai - by kind (bookmark, todo, profile, ai)
    - tj q t/w/m - by time (today, week, month)
    - tj q =context - by context
    - tj q :tag - by tag
    - tj q p=N - by priority
    - tj q s=value - by status
    - Composite: tj q p=1 s=active :urgent
    """
    try:
        repository = RepositoryFactory.get_repository()

        # Parse all filter arguments
        filters = args.filter if hasattr(args, 'filter') and args.filter else []

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
            if filter_arg in ['b', 'd', 'p', 'ai', 'r', 'c']:
                kind_map = {
                    'b': 'bookmark',
                    'd': 'todo',
                    'p': 'profile',
                    'ai': 'ai',
                    'r': 'memory',
                    'c': 'calendar'
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
                    print("Error: Empty context name in query.", file=sys.stderr)
                    return
                query_parts.append(f"context={context}")

            # Query by tag
            elif filter_arg.startswith(':'):
                tag = filter_arg[1:]
                if not tag:
                    print("Error: Empty tag name in query.", file=sys.stderr)
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
                    print("Error: Empty status value in query.", file=sys.stderr)
                    return
                query_parts.append(f"status={status}")

            else:
                print(f"Error: Unknown query filter '{filter_arg}'", file=sys.stderr)
                print("Usage: tj q [b|d|p|r|ai|t|w|m|=context|:tag|p=N|s=value]", file=sys.stderr)
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
            print(f"{i:2d}  {format_entry_for_display(entry)}")

        # Store numbered entries in state for numbered operations
        state = get_state()
        state["last_query_results"] = [e.id for e in active_entries]
        state["last_query_time"] = datetime.now().timestamp()
        save_state(state)

    except Exception as e:
        print(f"Query error: {e}", file=sys.stderr)
