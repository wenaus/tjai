import argparse
import sys
from typing import List, Optional, Tuple
from datetime import datetime

from tj.backup import auto_backup, list_backups, create_backup
from tj.commands.ai import handle_ai_command
from tj.commands.common import not_yet_implemented, handle_delete
from tj.commands.context import handle_context
from tj.commands.create import handle_creation
from tj.commands.delete import handle_delete_new
from tj.commands.dump import handle_dump
from tj.commands.list import handle_list_command, handle_list_all
from tj.commands.modify import (
    handle_add_subnote, handle_edit, handle_tag_command,
    handle_move, handle_show, handle_pin
)
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
    parser.add_argument('--test', action='store_true',
                       help='Use test database at tjai/test.db')

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

    # Dump
    p_dump = subparsers.add_parser('dump', help="Output database as executable tj commands.")
    p_dump.set_defaults(func=handle_dump)

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

    # Check for heredoc input: <<! (can appear anywhere in args)
    if '<<!' in sys.argv:
        heredoc_index = sys.argv.index('<<!')

        # Everything before <<! are parameters (like at=20251115/10:00)
        params_before = sys.argv[1:heredoc_index]

        # Everything after <<! are additional args (rare, but supported)
        params_after = sys.argv[heredoc_index + 1:] if heredoc_index + 1 < len(sys.argv) else []

        # Parse heredoc content from stdin
        content = parse_heredoc()
        if content is None:
            return  # Error already printed

        # Combine all parameters and extract at= timestamp
        all_params = params_before + params_after
        timestamp_override, filtered_args = parse_at_timestamp(all_params)

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
        # Process global flags and remove from sys.argv before command parsing
        test_mode = False
        flags_to_remove = []

        for arg in sys.argv[1:]:
            if arg == '--test':
                test_mode = True
                flags_to_remove.append(arg)
            # Future flags: --verbose, --debug, --dry-run, etc.

        check_virtual_environment()
        init_db()

        # Remove processed flags from sys.argv AFTER init_db() so database.py can see them
        for flag in flags_to_remove:
            sys.argv.remove(flag)

        # Auto-backup on every command execution (skip in test mode)
        if not test_mode:
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
