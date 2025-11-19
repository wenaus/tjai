"""List command handlers for tj."""

import sys
from datetime import datetime

from tj.colors import colorize_content
from tj.repository_factory import RepositoryFactory
from tj.state import get_state
from tj.timezone_manager import format_time_dashboard


def handle_list_command(args) -> None:
    """Handle the list command."""
    try:
        repository = RepositoryFactory.get_repository()
        list_type = args.list_type

        if not list_type:
            # Default: list memories
            entries = repository.query_entries(kind='memory')
            active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

            if not active_entries:
                print("No memories found.")
                return

            print(f"{len(active_entries)} memories:")
            for i, entry in enumerate(sorted(active_entries, key=lambda e: e.timestamp_created, reverse=True), 1):
                # Format timestamp in dashboard style
                time_str = format_time_dashboard(entry.timestamp_created)
                content_colored = colorize_content(entry.content)

                context_str = f" [{entry.context}]" if entry.context else ""
                print(f"{i:2d}  {time_str} {content_colored}{context_str}")
            return

        if list_type == 'c':  # contexts
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

        elif list_type == 't':  # tags
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

        elif list_type in ['p', 'b', 'd', 'ai']:  # profile, bookmarks, todos, AI guidelines
            type_map = {'p': 'profile', 'b': 'bookmark', 'd': 'todo', 'ai': 'ai'}
            entry_type = type_map[list_type]
            type_display = {'p': 'profiles', 'b': 'bookmarks', 'd': 'todos', 'ai': 'AI guidelines'}[list_type]

            entries = repository.query_entries(kind=entry_type)
            active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

            if not active_entries:
                print(f"No {type_display} found.")
                return

            print(f"{len(active_entries)} {type_display}:")
            for i, entry in enumerate(sorted(active_entries, key=lambda e: e.timestamp_created, reverse=True), 1):
                # Format timestamp in dashboard style
                time_str = format_time_dashboard(entry.timestamp_created)
                content_colored = colorize_content(entry.content)

                context_str = f" [{entry.context}]" if entry.context else ""
                print(f"{i:2d}  {time_str} {content_colored}{context_str}")

    except Exception as e:
        print(f"List error: {e}", file=sys.stderr)


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
            time_str = format_time_dashboard(entry.timestamp_created)

            context_str = f" [{entry.context}]" if entry.context else ""
            if entry.kind == 'todo':
                print(f"{i:2d}  {time_str} ToDo: {content_colored}{context_str}")
            elif entry.kind in ['memory', 'bookmark']:
                print(f"{i:2d}  {time_str} {content_colored}{context_str}")
            else:
                print(f"{i:2d}  {time_str} [{entry.kind}] {content_colored}{context_str}")

    except Exception as e:
        print(f"List all error: {e}", file=sys.stderr)
