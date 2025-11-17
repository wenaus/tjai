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
    """
    try:
        repository = RepositoryFactory.get_repository()

        # No filter = all entries
        if not hasattr(args, 'filter') or not args.filter:
            entries = repository.query_entries()
            query_desc = "all entries"
        else:
            filter_arg = args.filter[0] if isinstance(args.filter, list) else args.filter
            entries = []
            query_desc = ""

            # Query by kind
            if filter_arg in ['b', 'd', 'p', 'ai']:
                kind_map = {
                    'b': 'bookmark',
                    'd': 'todo',
                    'p': 'profile',
                    'ai': 'ai'
                }
                kind = kind_map[filter_arg]
                entries = repository.query_entries(kind=kind)
                query_desc = f"kind={kind}"

            # Query by time
            elif filter_arg in ['t', 'w', 'm']:
                now = datetime.now()
                if filter_arg == 't':  # today
                    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    cutoff = start_of_day.timestamp()
                    query_desc = "today"
                elif filter_arg == 'w':  # week
                    start_of_week = now - timedelta(days=7)
                    cutoff = start_of_week.timestamp()
                    query_desc = "last 7 days"
                elif filter_arg == 'm':  # month
                    start_of_month = now - timedelta(days=30)
                    cutoff = start_of_month.timestamp()
                    query_desc = "last 30 days"

                all_entries = repository.query_entries()
                entries = [e for e in all_entries if e.timestamp_created >= cutoff]

            # Query by context
            elif filter_arg.startswith('='):
                context_name = filter_arg[1:]
                if not context_name:
                    print("Error: Empty context name in query.", file=sys.stderr)
                    return
                entries = repository.query_entries(context=context_name)
                query_desc = f"context={context_name}"

            # Query by tag
            elif filter_arg.startswith(':'):
                tag_name = filter_arg[1:]
                if not tag_name:
                    print("Error: Empty tag name in query.", file=sys.stderr)
                    return
                entries = repository.query_entries(tag=tag_name)
                query_desc = f"tag={tag_name}"

            else:
                print(f"Error: Unknown query filter '{filter_arg}'", file=sys.stderr)
                print("Usage: tj q [b|d|p|ai|t|w|m|=context|:tag]", file=sys.stderr)
                return

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
