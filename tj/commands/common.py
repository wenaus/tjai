import sys
from datetime import datetime
from typing import Optional, Tuple, List

from tj.config import get_recent_entries_hours
from tj.repository import Entry
from tj.state import display_context, get_state, save_state


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


def extract_context_from_args(entry_arg: str, remaining_args: List[str]) -> Tuple[Optional[str], List[str]]:
    """Extract context marker from arguments if present.

    Handles patterns like: tj e =context underway

    Args:
        entry_arg: First argument (might be =context)
        remaining_args: Remaining arguments

    Returns:
        Tuple of (entry_identifier, remaining_args)
        If context was found, entry_identifier is first remaining_arg
    """
    if entry_arg and entry_arg.startswith('='):
        context_name = entry_arg[1:]

        # Set context
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
        from tj.repository_factory import RepositoryFactory
        from tj.state import get_state

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

                    # Multiple matches - prompt user to choose
                    print(f"\nMultiple entries found with name '@{name}':")
                    for i, entry in enumerate(matching_entries, 1):
                        context_str = f"={entry.context}" if entry.context else "(no context)"
                        # Show first 50 chars of content
                        content_preview = entry.content[:50] + "..." if len(entry.content) > 50 else entry.content
                        print(f"  {i}. {context_str}: {content_preview}")

                    choice = input(f"\nSelect entry (1-{len(matching_entries)}) or 'c' to cancel: ").strip().lower()

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


def format_entry_for_display(entry) -> str:
    """Format an entry for display with timestamp, content, and context."""
    from tj.timezone_manager import format_time_dashboard
    from tj.colors import colorize_content, colorize_context, colorize_timestamp

    time_str = colorize_timestamp(format_time_dashboard(entry.timestamp_created))
    content_colored = colorize_content(entry.content)
    context_str = f" {colorize_context(entry.context)}" if entry.context else ""

    return f"{time_str} {content_colored}{context_str}"

def not_yet_implemented(args, num_identifier: Optional[int] = None) -> None:
    display_context()
    if num_identifier:
        print(f"Command for item {num_identifier} is not yet implemented.", file=sys.stderr)
    else:
        print("This command is not yet implemented.", file=sys.stderr)

def handle_delete(args, num_identifier: Optional[int] = None) -> None:
    display_context()
    
    identifier = num_identifier if num_identifier else getattr(args, 'identifier', None)
    
    if not identifier:
        print("Error: Please provide an entry number or ID to delete.", file=sys.stderr)
        return

    try:
        from tj.repository_factory import RepositoryFactory
        from tj.state import get_state
        import uuid
        
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
            from datetime import datetime
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
        
        from tj.timezone_manager import get_current_timezone, format_time_in_timezone
        current_tz = get_current_timezone()
        time_str = format_time_in_timezone(entry_to_delete.timestamp_created, current_tz)
        
        print(f"Entry to delete:")
        print(f"  {content_preview}")
        print(f"  {time_str}")
        if entry_to_delete.context:
            print(f"  Context: {entry_to_delete.context}")
        print(f"  Type: {entry_to_delete.kind}")
        
        # Ask for confirmation
        response = input("\nDelete this entry? [y/N]: ").strip().lower()
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
