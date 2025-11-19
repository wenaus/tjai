import sys
from datetime import datetime
from typing import Optional

from tj.config import get_recent_entries_hours
from tj.repository import Entry
from tj.state import display_context


def get_entry_from_recent_list(entry_identifier) -> Optional[Entry]:
    """Get entry from recent list by number or @name.

    Args:
        entry_identifier: Either an integer (entry number) or string starting with @ (name)

    Returns the entry if found, None otherwise.
    """
    try:
        from tj.repository_factory import RepositoryFactory
        from tj.state import get_state

        repository = RepositoryFactory.get_repository()

        # Check if it's a @name reference
        if isinstance(entry_identifier, str) and entry_identifier.startswith('@'):
            name = entry_identifier[1:]  # Remove @
            state = get_state()
            current_context = state.get("current_context")
            return repository.get_entry_by_name(name, current_context)

        # Otherwise treat as entry number
        entry_num = int(entry_identifier)

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
    from tj.colors import colorize_content, colorize_context

    time_str = format_time_dashboard(entry.timestamp_created)
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
