"""List command handlers for tj."""

import sys
import time
import traceback
from datetime import datetime, timedelta

from tj.colors import colorize_content, colorize_context, colorize_kind, colorize_timestamp, colorize_creation_timestamp, colorize_entry_number, BOLD, RESET
from tj.commands.common import truncate_content
from tj.options import debug_time
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state, display_context
from tj.timezone_manager import format_time_dashboard


def handle_list_command(args) -> None:
    """Handle the list command with optional filters.

    Supports:
    - tj l - all entries
    - tj l c - list contexts (metadata)
    - tj l t - list tags (metadata)
    - tj l @ - list named entries (metadata)
    - tj l d - list todos
    - tj l =context - entries in context
    - tj l :tag - entries with tag
    - tj l p=1 s=active - composite filters
    - tj l 10 - last 10 entries
    - tj l -10 - first 10 entries
    - tj l 7d - last 7 days
    - tj l 1d - today (last 24 hours)
    """
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
        clean_mode = getattr(args, 'clean', False)
        no_truncate = getattr(args, 'no_truncate', False) or getattr(args, 'all', False) or clean_mode
        _list_entries_with_filters(repository, filters, no_truncate, clean_mode)

    except Exception as e:
        traceback.print_exc()
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

    # Get entry counts in one query (instead of per-context)
    entry_counts = repository.get_entry_counts_by_context()

    print(f"Contexts ({len(context_entities)}):")
    for i, context in enumerate(sorted(context_entities, key=lambda c: c.name), 1):
        count = entry_counts.get(context.name, 0)

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
        display_parts.append(f"{count} entries")
        print(" - ".join(display_parts))


def _list_named_entries(repository):
    """List all named entries."""
    from tj.commands.common import get_safe_exclude_tags
    all_entries = repository.query_entries(exclude_tags=get_safe_exclude_tags())
    named_entries = [e for e in all_entries if e.name and not getattr(e, 'deleted_at', None)]

    if not named_entries:
        print("No named entries found.")
        return

    # Sort by modification time, oldest first (newest at bottom)
    named_entries.sort(key=lambda e: e.timestamp_modified, reverse=False)

    print(f"Named entries ({len(named_entries)}):")
    for i, entry in enumerate(named_entries, 1):
        time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_modified))
        content_preview = truncate_content(entry.content)
        # Prepend bold name with @ symbol to content
        content_preview = f"{BOLD}@{entry.name}{RESET}  {content_preview}"
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


def _list_entries_with_filters(repository, filters, no_truncate=False, clean_mode=False):
    """List entries with composite filters.

    Context behavior:
    - No =context filter: uses active context (if set)
    - =0 filter: context-neutral (shows all entries regardless of context)
    - =<name> filter: shows entries in that specific context
    """
    time_filter_start = time.time()

    # Build query parameters
    kind = None
    context = None
    context_neutral = False  # True if =0 was specified (show all)
    tag = None
    exclude_tags = []
    priority = None
    status = None
    status_exclude = 'archive'  # Default: hide archived entries
    time_cutoff = None
    text_filter = None
    max_entries = None
    filter_by_priority = False  # True if 'priority' filter specified
    query_parts = []

    # Check if any explicit context filter is provided
    has_context_filter = any(f.startswith('=') for f in filters)

    for filter_arg in filters:
        # Check for days filter (e.g., 10d = last 10 days)
        if filter_arg.endswith('d') and filter_arg[:-1].isdigit():
            days = int(filter_arg[:-1])
            now = datetime.now()
            days_ago = now - timedelta(days=days)
            time_cutoff = days_ago.timestamp()
            query_parts.append(f"last {days} day{'s' if days != 1 else ''}")
            continue

        # Check for numeric entry limit (positive or negative)
        if filter_arg.lstrip('-').isdigit():
            max_entries = int(filter_arg)
            if max_entries < 0:
                query_parts.append(f"first {abs(max_entries)}")
            else:
                query_parts.append(f"limit {max_entries}")
            continue

        # Special filter: 'archive' shows archived entries
        if filter_arg == 'archive':
            status = 'archive'
            status_exclude = None  # Don't exclude archived when explicitly requesting
            query_parts.append('archive')
            continue

        # Special filter: 'priority' shows all prioritized entries sorted by priority
        if filter_arg == 'priority':
            filter_by_priority = True
            query_parts.append('priority')
            continue

        # Query by kind
        from tj.commands.common import ENTRY_TYPE_MAP
        if filter_arg in ENTRY_TYPE_MAP:
            kind = ENTRY_TYPE_MAP[filter_arg]
            query_parts.append(kind)

        # Query by context
        elif filter_arg.startswith('='):
            context_value = filter_arg[1:]
            if not context_value:
                print("Error: Empty context name.", file=sys.stderr)
                return
            if context_value == '0':
                # =0 means context-neutral (show all entries)
                context_neutral = True
            else:
                context = context_value
                query_parts.append(f"{filter_arg}")

        # Query by tag (exclude)
        elif filter_arg.startswith('-:'):
            exclude_tag = filter_arg[2:]
            if not exclude_tag:
                print("Error: Empty exclude tag name.", file=sys.stderr)
                return
            exclude_tags.append(exclude_tag)
            query_parts.append(f"{filter_arg}")

        # Query by tag (include)
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

    # Apply active context if no explicit context filter was provided
    if not has_context_filter and not context_neutral:
        state = get_state()
        active_context = state.get("current_context")
        if active_context:
            context = active_context
            query_parts.append(f"={active_context}")

    # Execute query (add safe mode exclude if active)
    from tj.commands.common import get_safe_exclude_tags
    safe_exclude_tags = get_safe_exclude_tags(exclude_tags if exclude_tags else None)

    debug_time("list_parse_filters", time_filter_start)
    time_query_start = time.time()

    entries = repository.query_entries(
        kind=kind,
        context=context,
        tag=tag,
        priority=priority,
        status=status,
        status_exclude=status_exclude,
        exclude_tags=safe_exclude_tags
    )
    debug_time("list_query_entries", time_query_start)

    # Apply time filter (post-query)
    if time_cutoff:
        entries = [e for e in entries if e.timestamp_created >= time_cutoff]

    # Apply text filter (post-query, case-insensitive)
    if text_filter:
        entries = [e for e in entries if text_filter in e.content.lower()]

    # Build query description
    if query_parts:
        from tj.commands.common import ENTRY_TYPE_ABBREV
        # Check if it's just a kind filter for cleaner display
        if len(query_parts) == 1 and query_parts[0] in ENTRY_TYPE_ABBREV.keys():
            query_desc = f"{query_parts[0]}"
        else:
            query_desc = " ".join(query_parts)
    else:
        query_desc = "all"

    # Filter out deleted entries
    active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

    # Filter to prioritized entries if 'priority' filter specified
    if filter_by_priority:
        active_entries = [e for e in active_entries if e.priority is not None]

    # Sort: by priority ascending if priority filter, otherwise by timestamp
    # Within same priority, oldest first (newest at bottom) - consistent with standard listing
    if filter_by_priority:
        active_entries.sort(key=lambda e: (e.priority, e.timestamp_modified))
    else:
        active_entries.sort(key=lambda e: e.timestamp_modified, reverse=False)

    # Apply max_entries limit
    if max_entries and len(active_entries) > abs(max_entries):
        if max_entries < 0:
            # Negative: show first N (oldest)
            active_entries = active_entries[:abs(max_entries)]
        else:
            # Positive: show last N (most recent)
            active_entries = active_entries[-max_entries:]

    # Display results
    if not active_entries:
        print(f"No {query_desc} entries found")
        return

    # Print legend and headers (skip in clean mode)
    if not clean_mode:
        from tj.colors import BRIGHT_YELLOW, RESET
        filter_desc = f" ({query_desc})" if query_desc else ""
        print(f"{BRIGHT_YELLOW}========== tj entries{filter_desc} =========={RESET}")
        legend = "Entry types: [ai]=AI guidance [b]=bookmark [do]=todo [j]=journal [m]=memory [p]=profile"
        print(legend)

        # Add metadata line
        print("Metadata: =context :tag @name p=priority s=status")

        # Add column headers with timezone info
        from tj.timezone_manager import get_current_timezone
        tz = get_current_timezone()
        print(f"Entry   Timestamp       Type Content     (TZ: {tz})")
    # Get truncate length from config
    from tj.config import get_content_truncate_length
    from tj.commands.common import format_entry_for_display
    truncate_len = get_content_truncate_length()

    # AI guidelines should never be truncated (meant to be read in full)
    skip_truncate = (kind == 'ai_guideline')

    # Get all tags once (not per entry)
    tags_by_entry = repository.get_tags_by_entry()

    # Load known contexts for orphan detection during display loop
    known_contexts = {c.name for c in repository.get_all_contexts()}
    missing_contexts = set()

    time_format_start = time.time()
    for i, entry in enumerate(active_entries, 1):
        # Track missing contexts during display (no extra scan)
        if entry.context and entry.context not in known_contexts:
            missing_contexts.add(entry.context)

        # Check no_truncate flag (from 'tj a' command)
        if no_truncate:
            entry_truncate = None
        # Check for per-entry truncate setting, otherwise use global
        elif entry.data and 'truncate_lines' in entry.data:
            entry_truncate = int(entry.data['truncate_lines'])
        elif skip_truncate:
            entry_truncate = None
        else:
            entry_truncate = truncate_len
        entry_tags = tags_by_entry.get(entry.id, [])
        if clean_mode:
            print(entry.content)
        else:
            print(format_entry_for_display(entry, i, entry_truncate, entry_tags))
    debug_time("list_format_entries", time_format_start)

    # Auto-create any orphaned contexts found during display
    if missing_contexts:
        from tj.repository import Context
        from tj.colors import RED, BOLD, RESET
        now = datetime.now().timestamp()
        for ctx_name in missing_contexts:
            repository.create_context(Context(
                name=ctx_name,
                title=None,
                description=None,
                timestamp_created=now,
                timestamp_modified=now
            ))
        print(f"{RED}{BOLD}Created missing contexts: {', '.join(sorted(missing_contexts))}{RESET}")

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

        from tj.commands.common import format_entry_for_display
        for i, entry in enumerate(sorted_entries, 1):
            print(format_entry_for_display(entry, i))

    except Exception as e:
        traceback.print_exc()
        print(f"List all error: {e}", file=sys.stderr)
