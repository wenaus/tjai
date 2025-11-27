import sys
import json
import socket
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, List

from tj.colors import (colorize_content, colorize_context, colorize_creation_timestamp,
                       colorize_entry_number, colorize_kind, colorize_priority, colorize_timestamp,
                       BOLD, RESET, RED, TERRACOTTA, LIGHT_GOLD)
from tj.config import get_recent_entries_hours
from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import display_context, get_state, save_state
from tj.timezone_manager import format_time_dashboard, get_timezone_object, get_current_timezone, format_time_in_timezone


def log_operation(operation: str, result: str, details: dict = None) -> None:
    """Log an admin/system operation to the database.

    Args:
        operation: Operation name (e.g., 'backup', 'purge', 'safe', 'normal', 'lines')
        result: Result status ('success', 'error', 'cancelled')
        details: Optional dict with operation-specific details
    """
    try:
        repository = RepositoryFactory.get_repository()

        # Build log entry content as JSON
        log_data = {
            'operation': operation,
            'hostname': socket.gethostname(),
            'result': result,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        if details:
            log_data['details'] = details

        # Build human-readable summary
        if result == 'success':
            result_text = result
        else:
            result_text = f"{RED}{BOLD}failed{RESET}"

        # Build summary with operation-specific formatting
        if operation == 'lines' and details and 'old_value' in details and 'new_value' in details:
            summary = f"Changed truncation lines from {details['old_value']} to {details['new_value']}"
        else:
            summary = f"{operation}: {result_text}"
            if details:
                if operation == 'backup' and 'size_kb' in details:
                    summary += f" ({details['size_kb']} KB)"
                elif operation == 'purge' and 'entries_deleted' in details:
                    summary += f" ({details['entries_deleted']} entries, {details['tags_deleted']} tags, {details['space_reclaimed_kb']} KB)"
                elif operation == 'purge' and 'entries_found' in details:
                    summary += f" ({details['entries_found']} entries found)"

        content = summary + "\n" + json.dumps(log_data, indent=2)

        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).timestamp()

        entry = Entry(
            id=entry_id,
            content=content,
            kind='log',
            timestamp_created=now,
            timestamp_modified=now,
            data={'truncate_lines': 0}  # Only show summary line in list view
        )

        repository.create_entry(entry)

    except Exception as e:
        print(f"Warning: Failed to log operation: {e}", file=sys.stderr)


def should_log_backup(success: bool) -> bool:
    """Check if auto backup should be logged (first of day or failure).

    Args:
        success: Whether backup succeeded

    Returns:
        True if should log, False otherwise
    """
    if not success:
        return True  # Always log failures

    try:
        repository = RepositoryFactory.get_repository()

        # Get today's start (midnight UTC)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        # Check if any backup log from today
        log_entries = repository.query_entries(kind='log')
        for entry in log_entries:
            if entry.timestamp_created >= today_start:
                if entry.content.split('\n', 1)[0].startswith('backup:'):
                    return False  # Already logged today

        return True  # First backup of day

    except Exception:
        return False  # Don't log on error


# Entry type abbreviations for display and filenames
ENTRY_TYPE_ABBREV = {
    'journal': 'j',
    'memory': 'm',
    'profile': 'p',
    'bookmark': 'b',
    'todo': 'do',
    'ai': 'ai',
    'list': 'l',
    'log': 'log'
}

# Bidirectional map: accepts both abbreviation and full name, returns full name
# Built from ENTRY_TYPE_ABBREV
ENTRY_TYPE_MAP = {}
for _full_name, _abbrev in ENTRY_TYPE_ABBREV.items():
    ENTRY_TYPE_MAP[_abbrev] = _full_name  # j -> journal
    ENTRY_TYPE_MAP[_full_name] = _full_name  # journal -> journal


def get_safe_exclude_tags(additional_excludes: Optional[List[str]] = None) -> Optional[List[str]]:
    """Get exclude_tags list with 'private' added if safe mode is active.

    Args:
        additional_excludes: Optional list of additional tags to exclude

    Returns:
        List of tags to exclude, or None if no exclusions needed
    """
    state = get_state()
    exclude_tags = []

    # Add 'private' if safe mode is active
    if state.get("safe_mode"):
        exclude_tags.append("private")

    # Add any additional excludes
    if additional_excludes:
        exclude_tags.extend(additional_excludes)

    return exclude_tags if exclude_tags else None


def extract_first_context_from_parts(parts: List[str]) -> Tuple[Optional[str], List[str], bool]:
    """Extract inline context from a list of parts (ONLY the first =text).

    Args:
        parts: List of string parts (e.g., ['=tjai', 'use', '==text', 'here'])

    Returns:
        Tuple of (context_name, filtered_parts, found_context):
        - context_name: Extracted context name (None if =0 or no context found)
        - filtered_parts: All parts except the first =context marker
        - found_context: True if a =context marker was found
    """
    context_name = None
    filtered_parts = []
    found_context = False

    for part in parts:
        if part.startswith('=') and not found_context:
            # Only process the first =context marker
            found_context = True
            ctx = part[1:]
            if ctx == '0':
                context_name = None
            elif ctx:
                context_name = ctx
            # Don't add this part to filtered_parts
        else:
            filtered_parts.append(part)

    return context_name, filtered_parts, found_context


def extract_context_from_args(entry_arg: str, remaining_args: List[str], set_context: bool = False) -> Tuple[Optional[str], List[str]]:
    """Extract context marker from arguments if present.

    Handles patterns like: tj e =context underway

    Args:
        entry_arg: First argument (might be =context)
        remaining_args: Remaining arguments
        set_context: If True, set the current context; if False, just extract without setting

    Returns:
        Tuple of (entry_identifier, remaining_args)
        If context was found, entry_identifier is first remaining_arg
    """
    if entry_arg and entry_arg.startswith('='):
        context_name = entry_arg[1:]

        # Set context only if requested
        if set_context:
            state = get_state()
            if context_name == '0':
                state["current_context"] = None
            else:
                state["current_context"] = context_name
            save_state(state)

        # First remaining arg becomes entry identifier
        if remaining_args:
            return remaining_args[0], remaining_args[1:]
        else:
            # Just setting context, no entry specified
            return None, []

    return entry_arg, remaining_args


def get_entry_from_recent_list(entry_identifier) -> Optional[Entry]:
    """Get entry from recent list by number or @name.

    Args:
        entry_identifier: Either an integer (entry number) or string (name, with or without @)

    Returns the entry if found, None otherwise.
    """
    try:
        repository = RepositoryFactory.get_repository()

        # Check if it's a name reference (string that's not a number)
        if isinstance(entry_identifier, str):
            try:
                # Try to parse as number first
                entry_num = int(entry_identifier)
            except ValueError:
                # It's a name - remove @ if present, or use as-is
                name = entry_identifier[1:] if entry_identifier.startswith('@') else entry_identifier

                # Check if we have a current context set
                state = get_state()
                current_context = state.get("current_context")

                if current_context is not None:
                    # Context is set - search ONLY in that context
                    entry = repository.get_entry_by_name(name, current_context)
                    return entry  # Will be None if not found in this context (caller can create new)
                else:
                    # No context set - search globally and disambiguate if needed
                    matching_entries = repository.get_entries_by_name(name)

                    if not matching_entries:
                        return None

                    if len(matching_entries) == 1:
                        # Only one match, return it directly
                        return matching_entries[0]

                    # Multiple matches - prompt user to choose (bypass buffer for interactive prompt)
                    print(f"\nMultiple entries found with name '@{name}':", file=sys.__stdout__, flush=True)
                    for i, entry in enumerate(matching_entries, 1):
                        context_str = f"={entry.context}" if entry.context else "(no context)"
                        # Show first 50 chars of content
                        content_preview = entry.content[:50] + "..." if len(entry.content) > 50 else entry.content
                        print(f"  {i}. {context_str}: {colorize_content(content_preview)}", file=sys.__stdout__, flush=True)

                    print(f"\nSelect entry (1-{len(matching_entries)}) or 'c' to cancel: ", end='', file=sys.__stdout__, flush=True)
                    choice = input().strip().lower()

                    if choice == 'c':
                        print("Cancelled.")
                        return None

                    try:
                        choice_num = int(choice)
                        if 1 <= choice_num <= len(matching_entries):
                            return matching_entries[choice_num - 1]
                        else:
                            print(f"Error: Invalid choice. Must be 1-{len(matching_entries)}.")
                            return None
                    except ValueError:
                        print("Error: Invalid input.")
                        return None
        else:
            # It's already a number
            entry_num = int(entry_identifier)

        # First check if there's a recent query result list
        state = get_state()
        if "last_query_results" in state and state["last_query_results"]:
            entry_ids = state["last_query_results"]
            if 1 <= entry_num <= len(entry_ids):
                entry_id = entry_ids[entry_num - 1]
                return repository.get_entry(entry_id)
            return None

        # Fallback to recent entries if no query results
        all_entries = repository.query_entries()
        active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]

        recent_hours = get_recent_entries_hours()
        recent_cutoff = datetime.now().timestamp() - (recent_hours * 60 * 60)
        recent_entries = [e for e in active_entries if e.timestamp_created >= recent_cutoff]
        recent_entries = sorted(recent_entries, key=lambda e: e.timestamp_created, reverse=True)

        if 1 <= entry_num <= len(recent_entries):
            return recent_entries[entry_num - 1]
        return None
    except Exception:
        return None


def format_entry_for_display(entry, entry_number=None, truncate_lines=None, tags=None) -> str:
    """Format an entry for display in list format with all metadata.

    Args:
        entry: The Entry object to format
        entry_number: Optional entry number to display (e.g., 1, 2, 3...)
        truncate_lines: Optional number of lines to show (first line always shown)
        tags: Optional list of tag names (if None, will query DB - avoid in loops)

    Returns:
        Formatted string for display
    """
    repository = RepositoryFactory.get_repository()

    # Format timestamp
    time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_modified))

    # Truncate content if requested
    content = entry.content
    if truncate_lines is not None:  # None means no truncation
        lines = content.split('\n')

        # Count visual lines (accounting for line wrapping at ~100 chars)
        visual_line_count = 0
        for line in lines:
            if len(line) == 0:
                visual_line_count += 1
            else:
                # Estimate visual lines: divide by 100 and round up
                visual_line_count += (len(line) + 99) // 100

        # truncate_lines=0 means show only first line
        # truncate_lines>0 means show that many lines if content exceeds it
        if truncate_lines == 0 or visual_line_count > truncate_lines:
            first_line = lines[0]
            # Calculate max chars for truncate_lines worth of display
            # 0 means show just one line (~100 chars), >0 means that many lines
            max_chars = 100 if truncate_lines == 0 else (truncate_lines * 100)

            # Truncate first line if it exceeds max_chars
            if len(first_line) > max_chars:
                content = first_line[:max_chars] + " [...]"
            else:
                content = first_line

            # Add line count indicator
            content += f"     {LIGHT_GOLD}[{visual_line_count} lines]{RESET}"

    # Colorize content
    content_colored = colorize_content(content)

    # Prepend bold @name if entry has one
    if entry.name:
        content_colored = f"{BOLD}@{entry.name}{RESET}  {content_colored}"

    # Prepend priority and status if present
    metadata_parts = []
    if entry.priority is not None:
        metadata_parts.append(colorize_priority(entry.priority))
    if entry.status:
        metadata_parts.append(f"s={entry.status}")
    if metadata_parts:
        metadata_str = " ".join(metadata_parts)
        content_colored = f"{metadata_str}  {content_colored}"

    # Kind display
    if entry.kind in ENTRY_TYPE_ABBREV:
        type_prefix = f"{colorize_kind(ENTRY_TYPE_ABBREV[entry.kind])} "
    else:
        type_prefix = ""

    # For journal entries with event_date, show event date/time with countdown
    event_date_str = ""
    if entry.kind == 'journal' and entry.data and 'event_date' in entry.data:
        tz = get_timezone_object()
        if tz:
            event_dt = datetime.fromtimestamp(entry.data['event_date'], tz=tz)
        else:
            event_dt = datetime.fromtimestamp(entry.data['event_date'])

        # If time is midnight (00:00), show just date with weekday
        if event_dt.hour == 0 and event_dt.minute == 0:
            event_date_str = f"{colorize_timestamp(event_dt.strftime('%a %m/%d'))} "
        else:
            # Show full date and time with weekday
            event_date_str = f"{colorize_timestamp(event_dt.strftime('%a %m/%d/%H:%M'))} "

        # Add "Today in Xh Ym" marker if event is today with a time
        now = datetime.now(tz) if tz else datetime.now()
        if event_dt.date() == now.date() and not (event_dt.hour == 0 and event_dt.minute == 0):
            time_diff = event_dt - now
            total_seconds = int(time_diff.total_seconds())

            if total_seconds > 0:  # Event is in the future
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60

                if hours > 0:
                    countdown_str = f"{hours}h {minutes}m"
                else:
                    countdown_str = f"{minutes}m"

                event_date_str += f"{TERRACOTTA}{BOLD}Today in {countdown_str}{RESET} "

    # Context
    context_str = f"{colorize_context(entry.context)} " if entry.context else ""

    # Tags - only append if not already in content
    if tags is None:
        tags = repository.get_tags(entry.id)
    missing_tags = []
    for tag in tags:
        if f':{tag}' not in content:  # Check original content, not colorized
            missing_tags.append(tag)
    tags_str = f" {colorize_content(' '.join(':' + t for t in missing_tags))}" if missing_tags else ""

    # Entry number prefix (optional)
    number_str = f"{colorize_entry_number(entry_number)}  " if entry_number else ""

    # Assemble the full line
    return f"{number_str}{time_str} {type_prefix}{event_date_str}{context_str}{content_colored}{tags_str}"

def not_yet_implemented(args, num_identifier: Optional[int] = None) -> None:
    if num_identifier:
        print(f"Command for item {num_identifier} is not yet implemented.", file=sys.stderr)
    else:
        print("This command is not yet implemented.", file=sys.stderr)

def handle_delete(args, num_identifier: Optional[int] = None) -> None:
    identifier = num_identifier if num_identifier else getattr(args, 'identifier', None)
    
    if not identifier:
        print("Error: Please provide an entry number or ID to delete.", file=sys.stderr)
        return

    try:
        repository = RepositoryFactory.get_repository()
        entry_to_delete = None

        # If identifier looks like a UUID, try to get it directly
        if isinstance(identifier, str) and len(identifier) == 36:
            try:
                uuid.UUID(identifier)  # validate UUID format
                entry_to_delete = repository.get_entry(identifier)
            except ValueError:
                pass

        # If not found or not a UUID, treat as entry number from recent list
        if not entry_to_delete and isinstance(identifier, (int, str)) and str(identifier).isdigit():
            # Get recent entries (same logic as status display)
            all_entries = repository.query_entries()
            active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]
            twenty_four_hours_ago = datetime.now().timestamp() - (24 * 60 * 60)
            recent_entries = [e for e in active_entries if e.timestamp_created >= twenty_four_hours_ago]
            recent_entries = sorted(recent_entries, key=lambda e: e.timestamp_created, reverse=True)
            
            entry_num = int(identifier)
            if 1 <= entry_num <= len(recent_entries):
                entry_to_delete = recent_entries[entry_num - 1]
        
        if not entry_to_delete:
            print(f"Error: Entry '{identifier}' not found.", file=sys.stderr)
            return
        
        # Show entry and ask for confirmation
        content_preview = entry_to_delete.content[:80] + "..." if len(entry_to_delete.content) > 80 else entry_to_delete.content

        current_tz = get_current_timezone()
        time_str = format_time_in_timezone(entry_to_delete.timestamp_created, current_tz)

        # Show entry details (bypass buffer for interactive prompt)
        print(f"Entry to delete:", file=sys.__stdout__, flush=True)
        print(f"  {colorize_content(content_preview)}", file=sys.__stdout__, flush=True)
        print(f"  {time_str}", file=sys.__stdout__, flush=True)
        if entry_to_delete.context:
            print(f"  Context: {entry_to_delete.context}", file=sys.__stdout__, flush=True)
        print(f"  Type: {entry_to_delete.kind}", file=sys.__stdout__, flush=True)

        # Ask for confirmation
        print("\nDelete this entry? [y/N]: ", end='', file=sys.__stdout__, flush=True)
        response = input().strip().lower()
        if response not in ['y', 'yes']:
            print("Delete cancelled.")
            return
        
        # Perform deletion (soft delete)
        success = repository.delete_entry(entry_to_delete.id)
        if success:
            print("Entry deleted successfully.")
        else:
            print("Error: Failed to delete entry.", file=sys.stderr)
            
    except Exception as e:
        print(f"Delete error: {e}", file=sys.stderr)
