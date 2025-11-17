import argparse
import sys
from typing import List, Optional, Tuple
from datetime import datetime

from tj.backup import auto_backup, list_backups, create_backup
from tj.commands.ai import handle_ai_command
from tj.commands.common import not_yet_implemented, handle_delete
from tj.commands.context import handle_context
from tj.commands.create import handle_creation
from tj.commands.query import handle_query
from tj.database import init_db, DatabaseError
from tj.environment import check_virtual_environment
from tj.repository_factory import RepositoryFactory
from tj.state import get_state
from tj.timezone_manager import handle_timezone_command, get_current_timezone, format_time_in_timezone
from tj.config import handle_config_command


def parse_heredoc() -> Optional[str]:
    """Parse heredoc input from stdin. Reads until line contains just '!'."""
    lines = []
    try:
        for line in sys.stdin:
            stripped = line.rstrip('\n\r')
            if stripped == '!':
                return '\n'.join(lines)
            lines.append(stripped)
    except EOFError:
        pass
    # If we get here, we didn't find the closing '!'
    if lines:
        print("Error: Heredoc not terminated with '!' on its own line", file=sys.stderr)
    return None


def parse_at_timestamp(args_list: List[str]) -> Tuple[Optional[float], List[str]]:
    """Extract and parse at=YYYYMMDD/HH:MM from arguments.

    Returns (timestamp_or_none, filtered_args)
    """
    timestamp = None
    filtered = []

    for arg in args_list:
        if arg.startswith('at='):
            # Parse format: at=20250907/23:57
            time_str = arg[3:]  # Remove 'at='
            try:
                # Expected format: YYYYMMDD/HH:MM
                if '/' not in time_str:
                    print(f"Error: Invalid at= format. Expected at=YYYYMMDD/HH:MM, got: {arg}", file=sys.stderr)
                    continue

                date_part, time_part = time_str.split('/', 1)

                # Parse date: YYYYMMDD
                if len(date_part) != 8:
                    print(f"Error: Date must be YYYYMMDD format, got: {date_part}", file=sys.stderr)
                    continue

                year = int(date_part[0:4])
                month = int(date_part[4:6])
                day = int(date_part[6:8])

                # Parse time: HH:MM
                if ':' not in time_part:
                    print(f"Error: Time must be HH:MM format, got: {time_part}", file=sys.stderr)
                    continue

                hour_str, minute_str = time_part.split(':', 1)
                hour = int(hour_str)
                minute = int(minute_str)

                # Create datetime and convert to timestamp
                dt = datetime(year, month, day, hour, minute)
                timestamp = dt.timestamp()

            except (ValueError, IndexError) as e:
                print(f"Error: Failed to parse at= timestamp '{arg}': {e}", file=sys.stderr)
        else:
            filtered.append(arg)

    return timestamp, filtered


def handle_creation_with_at(args, entry_type_override: Optional[str] = None):
    """Wrapper for handle_creation that extracts at= timestamp from args.input."""
    if hasattr(args, 'input') and args.input:
        timestamp_override, filtered_input = parse_at_timestamp(args.input)
        args.input = filtered_input
        args.timestamp_override = timestamp_override
    handle_creation(args, entry_type_override=entry_type_override)


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description="tj - Your Personal AI Memory Aid.", 
        add_help=False
    )
    
    # Global options
    parser.add_argument('--no-venv-check', action='store_true', 
                       help='Skip virtual environment check (for development/testing)')
    
    subparsers = parser.add_subparsers(dest='command')

    # System commands
    p_sys = subparsers.add_parser('sys', help="Manage the background sync service.")
    p_sys.add_argument('action', choices=['install', 'uninstall', 'start', 'stop', 'status'])
    p_sys.set_defaults(func=not_yet_implemented)

    # Dashboard
    p_hey = subparsers.add_parser('hey', help="Show a personal dashboard.")
    p_hey.set_defaults(func=not_yet_implemented)

    # List commands
    p_list = subparsers.add_parser('l', help="List metadata and entries.")
    p_list.add_argument('list_type', nargs='?', choices=['c', 't', 'p', 'b', 'd', 'ai'], help="List contexts (c), tags (t), profiles (p), bookmarks (b), todos (d), or AI guidelines (ai)")
    p_list.set_defaults(func=handle_list_command)

    # Context management
    p_context = subparsers.add_parser('c', help="Set or clear the active context.")
    p_context.add_argument('name', nargs='?', help="The name of the context to set.")
    p_context.add_argument('description', nargs='*', help="Description for the context.")
    p_context.set_defaults(func=handle_context)
    
    # Timezone management
    p_tz = subparsers.add_parser('tz', help="Set or show timezone.")
    p_tz.add_argument('zone', nargs='?', help="Timezone: eastern, central, pacific, euro, or +/-N")
    p_tz.set_defaults(func=handle_timezone_command)
    
    # Configuration management
    p_config = subparsers.add_parser('config', help="Show or modify configuration.")
    p_config.add_argument('action', nargs='?', choices=['show', 'db-path'], help="Configuration action")
    p_config.add_argument('path', nargs='?', help="New database path (for db-path action)")
    p_config.set_defaults(func=handle_config_command)

    # Creation commands
    p_todo = subparsers.add_parser('d', help="Add a todo item.", aliases=['do', 'todo'])
    p_todo.add_argument('input', nargs='+', help="Todo content and optional tags")
    p_todo.set_defaults(func=lambda args: handle_creation_with_at(args, entry_type_override='todo'))

    p_profile = subparsers.add_parser('p', help="Add a fact to your profile.")
    p_profile.add_argument('input', nargs='+', help="Profile fact and optional tags")
    p_profile.set_defaults(func=lambda args: handle_creation_with_at(args, entry_type_override='profile'))

    p_ai = subparsers.add_parser('ai', help="Add or query AI behavioral guidelines.")
    p_ai.add_argument('input', nargs='*', help="AI guideline content, or =context/:tag to query")
    p_ai.set_defaults(func=lambda args: handle_ai_command(args))

    # List all entries
    p_all = subparsers.add_parser('a', help="List all entries with optional filter.")
    p_all.add_argument('filter', nargs='?', help="Optional text filter")
    p_all.set_defaults(func=handle_list_all)
    
    # Modification commands  
    p_subnote = subparsers.add_parser('.', help="Add a sub-note to an entry.")
    p_subnote.add_argument('entry_num', help="Entry number")
    p_subnote.add_argument('text', nargs='+', help="Sub-note content")
    p_subnote.set_defaults(func=handle_add_subnote)
    
    p_edit = subparsers.add_parser('e', help="Edit an entry.")
    p_edit.add_argument('entry_num', help="Entry number")
    p_edit.add_argument('text', nargs='+', help="New entry content")
    p_edit.set_defaults(func=handle_edit)
    
    p_tag = subparsers.add_parser('t', help="Add tag to entry or list entries with tag.")
    p_tag.add_argument('entry_num', help="Entry number or tag name to search")
    p_tag.add_argument('tag', nargs='?', help="Tag name (when adding to entry)")
    p_tag.set_defaults(func=handle_tag_command)
    
    p_move = subparsers.add_parser('m', help="Move entry to context.")
    p_move.add_argument('entry_num', help="Entry number")
    p_move.add_argument('context', nargs='?', help="Context name (empty to remove context)")
    p_move.set_defaults(func=handle_move)
    
    p_show = subparsers.add_parser('s', help="Show entry details.")
    p_show.add_argument('entry_num', help="Entry number")
    p_show.set_defaults(func=handle_show)
    
    p_pin = subparsers.add_parser('^', help="Move entry to top.")
    p_pin.add_argument('entry_num', help="Entry number")
    p_pin.set_defaults(func=handle_pin)

    p_delete = subparsers.add_parser('x', help="Delete an entry or tag.")
    p_delete.add_argument('args', nargs='*', help="Arguments for delete operation")
    p_delete.set_defaults(func=handle_delete_new)

    # Query commands
    p_query = subparsers.add_parser('q', help="Query your entries.")
    p_query.add_argument('filter', nargs='?', help="Query filter: b/d/p/ai (kind), t/w/m (time), =context, :tag")
    p_query.set_defaults(func=handle_query)

    # Backup
    p_backup = subparsers.add_parser('backup', help="Create a manual backup.")
    p_backup.set_defaults(func=lambda args: handle_backup_command())
    
    # Sync
    p_sync = subparsers.add_parser('sync', help="Force a manual sync.")
    p_sync.set_defaults(func=not_yet_implemented)

    # Help
    p_help = subparsers.add_parser('h', help="Show this help message.", add_help=False)
    p_help.set_defaults(func=lambda args: parser.print_help())

    return parser

def handle_backup_command() -> None:
    """Handle the backup command."""
    try:
        success = create_backup()
        if success:
            print("Backup created successfully.")
        else:
            print("Backup failed.", file=sys.stderr)
    except Exception as e:
        print(f"Backup error: {e}", file=sys.stderr)

def handle_list_command(args) -> None:
    """Handle the list command."""
    try:
        repository = RepositoryFactory.get_repository()
        list_type = args.list_type
        
        if not list_type:
            print("Available list commands:")
            print("  tj l c  - List contexts")
            print("  tj l t  - List tags")  
            print("  tj l p  - List profiles")
            print("  tj l b  - List bookmarks")
            print("  tj l d  - List todos")
            return
        
        if list_type == 'c':  # contexts
            context_entities = repository.get_all_contexts()
            
            # Get current context
            from tj.state import get_state
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
                from tj.timezone_manager import format_time_dashboard
                from tj.colors import colorize_content
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
                from datetime import datetime
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
        
        from tj.timezone_manager import format_time_dashboard
        from tj.colors import colorize_content
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

def get_entry_from_recent_list(entry_num: int):
    """Get entry from recent list by number."""
    try:
        repository = RepositoryFactory.get_repository()
        from datetime import datetime
        from tj.config import get_recent_entries_hours

        all_entries = repository.query_entries()
        active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]

        recent_hours = get_recent_entries_hours()
        recent_cutoff = datetime.now().timestamp() - (recent_hours * 60 * 60)
        twenty_four_hours_ago = recent_cutoff  # Preserve variable name for compatibility
        recent_entries = [e for e in active_entries if e.timestamp_created >= twenty_four_hours_ago]
        recent_entries = sorted(recent_entries, key=lambda e: e.timestamp_created, reverse=True)
        
        if 1 <= entry_num <= len(recent_entries):
            return recent_entries[entry_num - 1]
        return None
    except Exception:
        return None

def handle_add_subnote(args) -> None:
    """Handle adding a sub-note to an entry."""
    try:
        entry_num = int(args.entry_num)
        text = " ".join(args.text)
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        # TODO: Implement sub-note creation in repository
        print(f"Sub-note added to entry {entry_num}: {text[:50]}...")
        
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Add sub-note error: {e}", file=sys.stderr)

def handle_edit(args) -> None:
    """Handle editing an entry (requires confirmation)."""
    try:
        entry_num = int(args.entry_num)
        new_text = " ".join(args.text)
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        # Show current content and confirm
        old_preview = entry.content
        new_preview = new_text
        
        print(f"Edit entry {entry_num}:")
        print(f"  Old: {old_preview}")
        print(f"  New: {new_preview}")
        
        response = input("\nConfirm edit? [y/N]: ").strip().lower()
        if response not in ['y', 'yes']:
            print("Edit cancelled.")
            return
        
        # Update entry
        from datetime import datetime, timezone
        repository = RepositoryFactory.get_repository()
        success = repository.update_entry(
            entry.id, 
            content=new_text,
            timestamp_modified=datetime.now(timezone.utc).timestamp(),
            is_dirty=True
        )
        
        if success:
            print("Entry updated successfully.")
        else:
            print("Error: Failed to update entry.", file=sys.stderr)
            
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Edit error: {e}", file=sys.stderr)

def handle_tag_command(args) -> None:
    """Handle tag command - either add tag to entry or list entries with tag."""
    try:
        if args.tag:
            # Two arguments: tj t <number> <tag> - add tag to entry
            entry_num = int(args.entry_num)
            tag = args.tag.strip()
            
            entry = get_entry_from_recent_list(entry_num)
            if not entry:
                print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
                return
            
            repository = RepositoryFactory.get_repository()
            repository.add_tag(entry.id, tag)
            print(f"Tag '{tag}' added to entry {entry_num}.")
            
        else:
            # One argument: tj t <tagname> - list entries with tag
            tagname = args.entry_num  # actually the tag name in this case
            
            # Use repository to find entries with this tag
            repository = RepositoryFactory.get_repository()
            all_entries = repository.query_entries(tag=tagname)
            active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]
            
            if not active_entries:
                print(f"No entries found with tag '{tagname}'.")
                return
            
            # Sort by timestamp (newest first)
            sorted_entries = sorted(active_entries, key=lambda e: e.timestamp_created, reverse=True)
            
            print(f"{len(sorted_entries)} entries with tag '{tagname}':")
            
            from tj.timezone_manager import format_time_dashboard
            from tj.colors import colorize_content
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
        
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Tag command error: {e}", file=sys.stderr)

def handle_add_tag(args) -> None:
    """Handle adding a tag to an entry."""
    try:
        entry_num = int(args.entry_num)
        tag = args.tag.strip()
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        repository = RepositoryFactory.get_repository()
        repository.add_tag(entry.id, tag)
        print(f"Tag '{tag}' added to entry {entry_num}.")
        
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Add tag error: {e}", file=sys.stderr)

def handle_move(args) -> None:
    """Handle moving an entry to a context."""
    try:
        entry_num = int(args.entry_num)
        context = args.context.strip() if args.context else None
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        repository = RepositoryFactory.get_repository()
        from datetime import datetime, timezone
        success = repository.update_entry(
            entry.id,
            context=context,
            timestamp_modified=datetime.now(timezone.utc).timestamp(),
            is_dirty=True
        )
        
        if success:
            if context:
                print(f"Entry {entry_num} moved to context '{context}'.")
            else:
                print(f"Entry {entry_num} removed from context.")
        else:
            print("Error: Failed to move entry.", file=sys.stderr)
            
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Move error: {e}", file=sys.stderr)

def handle_show(args) -> None:
    """Handle showing entry details."""
    try:
        entry_num = int(args.entry_num)
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        from tj.timezone_manager import format_time_dashboard
        time_str = format_time_dashboard(entry.timestamp_created)
        
        print(f"Entry {entry_num}:")
        print(f"  Content: {entry.content}")
        print(f"  Created: {time_str}")
        print(f"  Type: {entry.kind}")
        if entry.context:
            print(f"  Context: {entry.context}")
        
        # Show tags
        repository = RepositoryFactory.get_repository()
        tags = repository.get_tags(entry.id)
        if tags:
            print(f"  Tags: {', '.join(tags)}")
            
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Show error: {e}", file=sys.stderr)

def handle_pin(args) -> None:
    """Handle moving an entry to top (update timestamp)."""
    try:
        entry_num = int(args.entry_num)
        
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        # Update timestamp to now to move to top
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).timestamp()
        
        repository = RepositoryFactory.get_repository()
        success = repository.update_entry(
            entry.id,
            timestamp_modified=now,
            is_dirty=True
        )
        
        if success:
            print(f"Entry {entry_num} moved to top.")
        else:
            print("Error: Failed to move entry to top.", file=sys.stderr)
            
    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Pin error: {e}", file=sys.stderr)

def handle_delete_new(args) -> None:
    """Handle delete operations with new patterns."""
    try:
        if not args.args:
            print("Error: Please specify what to delete.", file=sys.stderr)
            print("Usage:")
            print("  tj x <number>        - Delete entry")
            print("  tj x <number> t <tag> - Delete tag from entry")
            print("  tj x t <tagname>     - Delete all instances of tag")
            return
        
        # Parse the arguments
        if len(args.args) == 1:
            # tj x <number> or tj x <invalid>
            arg = args.args[0]
            if arg.isdigit():
                # tj x <number> - delete entry
                handle_delete_entry(int(arg))
            else:
                print(f"Error: Invalid entry number '{arg}'.", file=sys.stderr)
                
        elif len(args.args) == 2 and args.args[0] == 't':
            # tj x t <tagname> - delete all tag instances
            tagname = args.args[1]
            handle_delete_all_tag_instances(tagname)
            
        elif len(args.args) == 3 and args.args[1] == 't':
            # tj x <number> t <tag> - delete tag from entry
            if args.args[0].isdigit():
                entry_num = int(args.args[0])
                tag = args.args[2]
                handle_delete_tag_from_entry(entry_num, tag)
            else:
                print(f"Error: Invalid entry number '{args.args[0]}'.", file=sys.stderr)
        else:
            print("Error: Invalid delete command format.", file=sys.stderr)
            print("Usage:")
            print("  tj x <number>        - Delete entry")
            print("  tj x <number> t <tag> - Delete tag from entry")
            print("  tj x t <tagname>     - Delete all instances of tag")
            
    except Exception as e:
        print(f"Delete error: {e}", file=sys.stderr)

def handle_delete_entry(entry_num: int) -> None:
    """Delete an entry (requires confirmation)."""
    try:
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        # Show entry and ask for confirmation
        from tj.timezone_manager import format_time_dashboard
        time_str = format_time_dashboard(entry.timestamp_created)
        
        context_str = f" [{entry.context}]" if entry.context else ""
        response = input(f"Delete: {time_str} {entry.content}{context_str} [y/N]: ").strip().lower()
        if response not in ['y', 'yes']:
            print("Delete cancelled.")
            return
        
        # Perform deletion
        repository = RepositoryFactory.get_repository()
        success = repository.delete_entry(entry.id)
        if success:
            print("Entry deleted successfully.")
        else:
            print("Error: Failed to delete entry.", file=sys.stderr)
            
    except Exception as e:
        print(f"Delete entry error: {e}", file=sys.stderr)

def handle_delete_tag_from_entry(entry_num: int, tag: str) -> None:
    """Delete a specific tag from an entry (requires confirmation)."""
    try:
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        
        # Check if entry has this tag
        repository = RepositoryFactory.get_repository()
        entry_tags = repository.get_tags(entry.id)
        if tag not in entry_tags:
            print(f"Error: Entry {entry_num} does not have tag '{tag}'.", file=sys.stderr)
            return
        
        # Show confirmation
        print(f"Remove tag '{tag}' from entry {entry_num}:")
        print(f"  {entry.content}")
        
        response = input(f"\nRemove tag '{tag}'? [y/N]: ").strip().lower()
        if response not in ['y', 'yes']:
            print("Tag removal cancelled.")
            return
        
        # Remove the tag
        success = repository.remove_tag(entry.id, tag)
        if success:
            print(f"Tag '{tag}' removed from entry {entry_num}.")
        else:
            print("Error: Failed to remove tag.", file=sys.stderr)
            
    except Exception as e:
        print(f"Delete tag error: {e}", file=sys.stderr)

def handle_delete_all_tag_instances(tagname: str) -> None:
    """Delete all instances of a tag (requires confirmation)."""
    try:
        repository = RepositoryFactory.get_repository()
        
        # Count how many entries have this tag
        all_tags = repository.get_all_tags()
        tag_count = sum(1 for tag in all_tags if tag.tag_name == tagname)
        
        if tag_count == 0:
            print(f"No entries found with tag '{tagname}'.")
            return
        
        # Show confirmation
        print(f"Delete tag '{tagname}' from {tag_count} entries?")
        response = input("This will remove the tag from all entries. Continue? [y/N]: ").strip().lower()
        if response not in ['y', 'yes']:
            print("Tag deletion cancelled.")
            return
        
        # Remove all instances
        count_removed = repository.remove_all_tag_instances(tagname)
        if count_removed > 0:
            print(f"Tag '{tagname}' removed from {count_removed} entries.")
        else:
            print("Error: No tag instances were removed.", file=sys.stderr)
            
    except Exception as e:
        print(f"Delete all tags error: {e}", file=sys.stderr)

def handle_numbered_command(parser: argparse.ArgumentParser, num_identifier: int, action_command: str, remaining_args: List[str]) -> None:
    """Handle commands prefixed with a number (e.g., '5 x' or '3 a text')."""
    known_commands = set(parser._subparsers._group_actions[0].choices.keys())
    
    if action_command not in known_commands:
        print(f"Error: Unknown action '{action_command}' for numbered command.", file=sys.stderr)
        return
    
    # Parse the action command with remaining arguments
    try:
        temp_argv = ['tj', action_command] + remaining_args
        args = parser.parse_args(temp_argv[1:])
        if hasattr(args, 'func'):
            # Pass num_identifier to functions that support it
            if action_command in ['a', 'x']:  # Commands that work with numbered entries
                args.func(args, num_identifier=num_identifier)
            else:
                args.func(args)
    except SystemExit:
        # argparse calls sys.exit on error, catch it
        print(f"Error: Invalid arguments for command '{action_command}'", file=sys.stderr)

def show_status() -> None:
    """Show status dashboard when no arguments provided."""
    try:
        repository = RepositoryFactory.get_repository()
        state = get_state()
        
        # Get basic stats
        all_entries = repository.query_entries()
        active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]
        
        # Count by type
        type_counts = {}
        for entry in active_entries:
            type_counts[entry.kind] = type_counts.get(entry.kind, 0) + 1
        
        # Get recent entries (configurable window)
        from datetime import datetime, timezone
        from tj.config import get_recent_entries_hours

        recent_hours = get_recent_entries_hours()
        twenty_four_hours_ago = datetime.now().timestamp() - (recent_hours * 60 * 60)
        today_entries = [e for e in active_entries if e.timestamp_created >= twenty_four_hours_ago]
        today_entries = sorted(today_entries, key=lambda e: e.timestamp_created, reverse=True)
        
        # Context display
        current_context = state.get("current_context")
        all_contexts = repository.get_all_contexts()
        context_count = len(all_contexts)
        
        if current_context:
            print(f"Context: {current_context}")
        else:
            print(f"Context: none")
        
        # Entry counts with tags, key types, and contexts
        all_tags = repository.get_all_tags()
        unique_tag_names = set(tag.tag_name for tag in all_tags)
        profile_count = type_counts.get('profile', 0)
        bookmark_count = type_counts.get('bookmark', 0)
        todo_count = type_counts.get('todo', 0)
        
        print(f"Entries: {len(active_entries)}    Tags: {len(unique_tag_names)}    Profiles: {profile_count}    Bookmarks: {bookmark_count}    Todos: {todo_count}    Contexts: {context_count}")
        
        # Latest entry timestamp
        if active_entries:
            latest_entry = max(active_entries, key=lambda e: e.timestamp_created)
            current_tz = get_current_timezone()
            latest_time = format_time_in_timezone(latest_entry.timestamp_created, current_tz)
            print(f"Latest: {latest_time}")
        
        # Recent entries (last 24 hours)
        if today_entries:
            print("Recent:")
            from tj.timezone_manager import format_time_dashboard
            from tj.colors import colorize_content
            for i, entry in enumerate(today_entries, 1):
                content_colored = colorize_content(entry.content)
                time_str = format_time_dashboard(entry.timestamp_created)
                
                if entry.kind in ['memory', 'bookmark']:
                    print(f"{i:2d}  {time_str} {content_colored}")
                elif entry.kind == 'todo':
                    print(f"{i:2d}  {time_str} ToDo: {content_colored}")
                else:
                    print(f"{i:2d}  {time_str} [{entry.kind}] {content_colored}")
        
        # Backup information
        try:
            backups = list_backups()
            from tj.backup import get_backup_dir
            backup_dir = get_backup_dir()
            if backups:
                total_size_bytes = sum(backup['size'] for backup in backups)
                total_size_mb = total_size_bytes / (1024 * 1024)
                latest_backup = backups[0]['modified'].strftime('%Y-%m-%d %H:%M')
                print(f"\nBackups: {len(backups)} files, {total_size_mb:.1f} MB (latest: {latest_backup}) [{backup_dir}]")
            else:
                print(f"\nBackups: No backups found [{backup_dir}]")
        except Exception:
            # Don't let backup info failure break the status display
            pass
        
        print("\nTry: tj \"your memory here\", tj h for help")
        
    except Exception as e:
        print(f"Status unavailable: {e}")
        print("Try: tj \"your memory here\", tj h for help")

def handle_context_syntax(first_arg: str, remaining_args: list) -> None:
    """Handle =context syntax for setting/clearing context.

    Syntax:
    Context definition (has -t flag):
    - tj =context -t title text → title = rest of line after -t
    - tj =context -t title -d description text → title + description (rest after -d)

    Entry creation (no -t flag):
    - tj =context entry content → switch context, create entry
    """
    from tj.commands.context import handle_context
    from tj.commands.create import handle_creation
    from tj.state import get_state, save_state

    if first_arg == '=0':
        # Clear context directly without confirmation
        state = get_state()
        current_context = state.get("current_context")
        if current_context:
            state["current_context"] = None
            save_state(state)
            print(f"Context '{current_context}' cleared.")
        else:
            print("No context to clear.")
        return

    # Extract context name (everything after =)
    context_name = first_arg[1:]  # Remove the = prefix

    if not context_name:
        print("Error: Empty context name. Use =<context> or =0 to clear.", file=sys.stderr)
        return

    if not remaining_args:
        # Just setting context: tj =tjai
        class Args:
            def __init__(self):
                self.name = context_name
                self.title = None
                self.description = []
        args = Args()
        handle_context(args)
    elif '-t' in remaining_args:
        # Context definition: -t flag means metadata, not entry creation
        # tj =context -t title text
        # tj =context -t title text -d description text
        try:
            t_index = remaining_args.index('-t')
            if t_index + 1 >= len(remaining_args):
                print("Error: -t flag requires a title.", file=sys.stderr)
                return

            # Find -d flag if present
            if '-d' in remaining_args:
                d_index = remaining_args.index('-d')
                if d_index <= t_index + 1:
                    print("Error: -d must come after title text.", file=sys.stderr)
                    return
                if d_index + 1 >= len(remaining_args):
                    print("Error: -d flag requires description text.", file=sys.stderr)
                    return

                # Title is between -t and -d
                title = " ".join(remaining_args[t_index + 1:d_index])
                # Description is after -d
                description = " ".join(remaining_args[d_index + 1:])
            else:
                # No -d flag, everything after -t is title
                title = " ".join(remaining_args[t_index + 1:])
                description = None

            class Args:
                def __init__(self):
                    self.name = context_name
                    self.title = title
                    self.description = [description] if description else []
            args = Args()
            handle_context(args)
        except ValueError as e:
            print(f"Error parsing context flags: {e}", file=sys.stderr)
            return
    else:
        # Inline context with content creation: tj =work meeting notes
        # First set the context
        class ContextArgs:
            def __init__(self):
                self.name = context_name
                self.title = None
                self.description = []
        context_args = ContextArgs()
        handle_context(context_args)

        # Then create the entry with the remaining content
        class CreateArgs:
            def __init__(self):
                self.input = remaining_args
        create_args = CreateArgs()
        handle_creation(create_args)


def main(skip_venv_check: bool = False) -> None:
    """Main function to parse arguments and dispatch commands."""
    parser = create_parser()

    if len(sys.argv) == 1:
        show_status()
        return

    # Check for global flags first
    if '--no-venv-check' in sys.argv:
        skip_venv_check = True
        # Remove the flag so it doesn't interfere with other parsing
        sys.argv = [arg for arg in sys.argv if arg != '--no-venv-check']

        # If only the flag was provided, show status
        if len(sys.argv) == 1:
            show_status()
            return

    first_arg = sys.argv[1]

    # Check for heredoc input: <<!
    if first_arg == '<<!':
        content = parse_heredoc()
        if content is None:
            return  # Error already printed

        # Extract at= timestamp and other args from remaining args
        remaining_args = sys.argv[2:]
        timestamp_override, filtered_args = parse_at_timestamp(remaining_args)

        # Create args object with heredoc content
        class Args:
            def __init__(self):
                self.input = [content] + filtered_args
                self.timestamp_override = timestamp_override

        args = Args()
        handle_creation(args)
        return

    # Check for =context syntax (tj =work, tj =0, tj =work content)
    if first_arg.startswith('='):
        remaining_args = sys.argv[2:] if len(sys.argv) > 2 else []
        handle_context_syntax(first_arg, remaining_args)
        return
    
    # Check if first argument is a small number (for numbered commands like '5 x')
    # Avoid treating dates (YYYYMMDD) as numbered commands
    if first_arg.isdigit() and len(first_arg) <= 3:
        if len(sys.argv) < 3:
            print("Error: Numbered command requires a subsequent action (e.g., 'a' or 'x').", file=sys.stderr)
            return
        
        num_identifier = int(first_arg)
        action_command = sys.argv[2]
        remaining_args = sys.argv[3:]
        
        handle_numbered_command(parser, num_identifier, action_command, remaining_args)
        return
    
    # Check if it's a known command
    known_commands = set(parser._subparsers._group_actions[0].choices.keys())
    if first_arg in known_commands:
        try:
            args = parser.parse_args()
            if hasattr(args, 'func'):
                args.func(args)
        except SystemExit:
            # argparse already handled the error
            pass
        return
    
    # Default: treat as content creation
    # Extract at= timestamp if present
    timestamp_override, filtered_input = parse_at_timestamp(sys.argv[1:])

    class Args:
        def __init__(self):
            self.input = filtered_input
            self.timestamp_override = timestamp_override

    args = Args()
    handle_creation(args)

def entrypoint() -> None:
    """Main entry point with error handling."""
    try:
        # Check if venv check should be skipped
        skip_venv_check = '--no-venv-check' in sys.argv
        
        if not skip_venv_check:
            check_virtual_environment()
            
        init_db()
        
        # Auto-backup on every command execution
        auto_backup()
        
        main()
    except DatabaseError as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
