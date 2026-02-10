import argparse
import sys
import time
import traceback
from typing import List, Optional, Tuple
from datetime import datetime

# Import consolidated options first (handles all CLI option parsing)
from tj.options import DEBUG, DB_PATH, FILE_CONTENT, get_command_args, get_original_argv
from tj.options import debug_time, debug_mark

_import_start = time.time()

from tj.database import init_db, DatabaseError

from tj.backup import auto_backup, list_backups
from tj.commands.ai import handle_ai_command
from tj.commands.calendar import handle_calendar_view
from tj.commands.common import not_yet_implemented, handle_delete, confirm_action
from tj.commands.context import handle_context
from tj.commands.create import handle_creation
from tj.commands.delete import handle_delete_new
from tj.commands.dump import handle_dump
from tj.commands.help import handle_help
from tj.commands.journal import handle_journal
from tj.commands.list import handle_list_command, handle_list_all
from tj.commands.lists import handle_add_list_item
from tj.commands.modify import (
    handle_edit, handle_tag_command, handle_untag_command,
    handle_move, handle_show, handle_pin
)
from tj.commands.copy import handle_copy
from tj.commands.subitems import handle_add_subitem
from tj.repository_factory import RepositoryFactory
from tj.state import get_state
from tj.timezone_manager import handle_timezone_command, get_current_timezone, format_time_in_timezone
from tj.config import handle_config_command, get_status_list_limit

debug_time("imports", _import_start)


def _ensure_agent_running() -> None:
    """Start agent if not recently active. Checks cached status to avoid latency."""
    import json
    from pathlib import Path

    # Check agent status file for recent activity
    status_file = Path.home() / ".tjai" / "agent_status.json"
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text())
            last_pull = status.get("last_pull")
            if last_pull and (time.time() - last_pull) < 30:
                # Agent was active within 30s, no action needed
                return
        except (json.JSONDecodeError, OSError):
            pass

    # Agent not recently active, try to start it
    try:
        from tj_agent.daemon import ensure_running
        ensure_running()
    except Exception as e:
        traceback.print_exc()
        print(f"Agent startup failed: {e}", file=sys.stderr)


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
        description="tj - A memory-augmenting knowledge DB and me descriptor",
        add_help=False
    )

    # Note: --db= is handled in entrypoint() before command parsing

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
    p_list.add_argument('filters', nargs=argparse.REMAINDER, help="Filters: c/t/@ (metadata), d/p/b/j/ai (type), =ctx, :tag, -:tag, p=N, s=val, priority, archive")
    p_list.add_argument('--clean', action='store_true', help="Output only entry content, no preamble")
    p_list.add_argument('--all', action='store_true', help="Show full content (no truncation)")
    p_list.set_defaults(func=handle_list_command)

    # List all (no truncation)
    p_all = subparsers.add_parser('a', help="List entries with full content (no truncation).")
    p_all.add_argument('filters', nargs=argparse.REMAINDER, help="Filters: same as 'l' command")
    p_all.set_defaults(func=handle_list_command, no_truncate=True)

    # Calendar view
    p_calendar = subparsers.add_parser('c', help="View calendar entries.")
    p_calendar.add_argument('timeframe', nargs='?', help="t/w/m with optional offset (e.g., 't+1', 'w 4', 'w -2')")
    p_calendar.add_argument('offset', nargs='?', help="Optional numeric offset for timeframe")
    p_calendar.set_defaults(func=lambda args: handle_calendar_view(args))

    # Yearly summary view
    from tj.commands.calendar import handle_yearly_summary
    p_year = subparsers.add_parser('y', help="Yearly calendar summary with month/week event counts.")
    p_year.set_defaults(func=lambda args: handle_yearly_summary(args))

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
    p_todo = subparsers.add_parser('do', help="Add a todo item.", aliases=['todo'])
    p_todo.add_argument('input', nargs='+', help="Todo content and optional tags")
    p_todo.set_defaults(func=lambda args: handle_creation_with_at(args, entry_type_override='todo'))

    p_profile = subparsers.add_parser('p', help="Add a fact to your profile.")
    p_profile.add_argument('input', nargs='+', help="Profile fact and optional tags")
    p_profile.set_defaults(func=lambda args: handle_creation_with_at(args, entry_type_override='profile'))

    p_ai = subparsers.add_parser('ai', help="Add or query AI behavioral guidelines.")
    p_ai.add_argument('input', nargs='*', help="AI guideline content, or =context/:tag to query")
    p_ai.set_defaults(func=lambda args: handle_ai_command(args))

    p_journal = subparsers.add_parser('j', help="Add calendar/journal entry with flexible date parsing.")
    p_journal.add_argument('input', nargs='+', help="Date/time spec and content (e.g., 'tomorrow meeting' or '16:30 dentist')")
    p_journal.set_defaults(func=handle_journal)

    # Modification commands
    p_subitem = subparsers.add_parser('.', help="Add sub-item to last parent.")
    p_subitem.add_argument('input', nargs='+', help="Sub-item content")
    p_subitem.set_defaults(func=handle_add_subitem)

    p_list_add = subparsers.add_parser('+', help="Add item to current list.")
    p_list_add.add_argument('input', nargs='+', help="List item text")
    p_list_add.set_defaults(func=handle_add_list_item)
    
    p_edit = subparsers.add_parser('e', help="Edit or create entry in editor.")
    p_edit.add_argument('entry_num', nargs='?', help="Entry number (optional, omit to create new entry)")
    p_edit.add_argument('text', nargs='*', help="New entry content (optional, omit to use editor)")
    p_edit.add_argument('-k', '--keep-time', action='store_true', help="Keep original modification time")
    p_edit.set_defaults(func=handle_edit)

    # Alias for editor creation
    p_edit_flag = subparsers.add_parser('-e', help="Create entry in editor.")
    p_edit_flag.set_defaults(func=lambda args: handle_edit(type('Args', (), {'entry_num': None, 'text': []})()))
    
    p_tag = subparsers.add_parser('t', help="Add tag to entry or list entries with tag.")
    p_tag.add_argument('entry_num', help="Entry number or tag name to search")
    p_tag.add_argument('tag', nargs='?', help="Tag name (when adding to entry)")
    p_tag.set_defaults(func=handle_tag_command)

    p_untag = subparsers.add_parser('t-', help="Remove tag from entry.")
    p_untag.add_argument('entry_num', help="Entry number")
    p_untag.add_argument('tag', help="Tag name to remove")
    p_untag.set_defaults(func=handle_untag_command)

    p_memory = subparsers.add_parser('m', help="Add a memory entry.")
    p_memory.add_argument('input', nargs='+', help="Entry content")
    p_memory.set_defaults(func=handle_creation)

    p_move = subparsers.add_parser('mv', help="Move entry to context.")
    p_move.add_argument('entry_num', help="Entry number")
    p_move.add_argument('context', nargs='?', help="Context name (empty to remove context)")
    p_move.set_defaults(func=handle_move)
    
    p_show = subparsers.add_parser('s', help="Show entry or context details.")
    p_show.add_argument('entry_num', nargs='?', help="Entry number or =context (default: latest)")
    p_show.set_defaults(func=handle_show)
    
    p_pin = subparsers.add_parser('^', help="Move entry to top.")
    p_pin.add_argument('entry_num', help="Entry number")
    p_pin.set_defaults(func=handle_pin)

    p_delete = subparsers.add_parser('d', help="Delete an entry or tag.")
    p_delete.add_argument('args', nargs='*', help="Arguments for delete operation")
    p_delete.set_defaults(func=handle_delete_new)

    # Archive/Unarchive
    from tj.commands.delete import handle_archive_command, handle_unarchive_command
    p_archive = subparsers.add_parser('archive', help="Archive an entry (hide from default listings).")
    p_archive.add_argument('args', nargs='*', help="Entry number(s) to archive")
    p_archive.set_defaults(func=handle_archive_command)

    p_unarchive = subparsers.add_parser('unarchive', help="Unarchive an entry (restore to default listings).")
    p_unarchive.add_argument('args', nargs='*', help="Entry number(s) to unarchive")
    p_unarchive.set_defaults(func=handle_unarchive_command)

    # Copy
    p_copy = subparsers.add_parser('cp', help="Copy an entry with new date/time.")
    p_copy.add_argument('args', nargs='*', help="Entry number, new date/time")
    p_copy.set_defaults(func=handle_copy)

    # Admin
    p_admin = subparsers.add_parser('admin', help="Admin commands for database maintenance.")
    p_admin.add_argument('subcommand', nargs='?', help="Admin subcommand (backup, purge, safe, normal, lines)")
    p_admin.add_argument('args', nargs='*', help="Additional arguments for admin subcommand")
    from tj.commands.admin import handle_admin
    p_admin.set_defaults(func=handle_admin)

    # Dump
    p_dump = subparsers.add_parser('dump', help="Output database as executable tj commands.")
    p_dump.set_defaults(func=handle_dump)

    # Sync
    p_sync = subparsers.add_parser('sync', help="Force a manual sync.")
    p_sync.set_defaults(func=not_yet_implemented)

    # Clock commands
    from tj.commands.clock import handle_clock_start, handle_clock_stop, handle_clock_break
    p_start = subparsers.add_parser('start', help="Start time clock in current context.")
    p_start.add_argument('time', nargs='?', help="Optional start time (e.g., '9am', '14:30')")
    p_start.set_defaults(func=handle_clock_start)

    p_stop = subparsers.add_parser('stop', help="Stop time clock.")
    p_stop.add_argument('time', nargs='?', help="Optional stop time (e.g., '5pm', '17:30')")
    p_stop.set_defaults(func=handle_clock_stop)

    p_break = subparsers.add_parser('break', help="Add break time to current clock session.")
    p_break.add_argument('duration', help="Break duration (e.g., '30' for minutes, '1h' for hours)")
    p_break.set_defaults(func=handle_clock_break)

    # Help
    p_help = subparsers.add_parser('h', help="Show this help message.", add_help=False)
    p_help.set_defaults(func=handle_help)

    return parser


def handle_numbered_command(parser: argparse.ArgumentParser, num_identifier: int, action_command: str, remaining_args: List[str]) -> None:
    """Handle commands prefixed with a number (e.g., '5 x' or '3 a text')."""
    from tj.commands.numbered import dispatch_numbered_command

    # Try metadata commands first (@name, p=N, s=status, k=kind, l=lines, :tag, =context)
    result = dispatch_numbered_command(num_identifier, action_command)
    if result is not None:
        return

    # Otherwise, handle as regular command
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
            if action_command in ['d']:  # Commands that work with numbered entries (delete)
                args.func(args, num_identifier=num_identifier)
            else:
                args.func(args)
    except SystemExit:
        # argparse calls sys.exit on error, catch it
        print(f"Error: Invalid arguments for command '{action_command}'", file=sys.stderr)

def show_status() -> None:
    """Show status dashboard when no arguments provided."""
    _t = time.time()
    # Show listing with configured limit of most recent entries (context-neutral)
    from tj.commands.list import handle_list_command

    class ListArgs:
        filters = [str(get_status_list_limit()), '=0']

    handle_list_command(ListArgs())
    debug_time("list_entries", _t)

    # Add additional summary stats
    _t = time.time()
    try:
        repository = RepositoryFactory.get_repository()
        state = get_state()

        # Get basic stats (apply safe mode filtering)
        from tj.commands.common import get_safe_exclude_tags
        all_entries = repository.query_entries(exclude_tags=get_safe_exclude_tags())
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

        # Get context info for counts
        all_contexts = repository.get_all_contexts()
        context_count = len(all_contexts)
        
        # Entry counts with tags, key types, and contexts
        all_tags = repository.get_all_tags()
        unique_tag_names = set(tag.tag_name for tag in all_tags)
        profile_count = type_counts.get('profile', 0)
        bookmark_count = type_counts.get('bookmark', 0)
        todo_count = type_counts.get('todo', 0)
        
        deleted_count = len(all_entries) - len(active_entries)
        print(f"\nSummary: {len(active_entries)} active, {len(all_entries)} total, {deleted_count} deleted | {len(unique_tag_names)} tags, {context_count} contexts")
        debug_time("summary_stats", _t)

        # Latest entry timestamp
        if active_entries:
            latest_entry = max(active_entries, key=lambda e: e.timestamp_created)
            current_tz = get_current_timezone()
            latest_time = format_time_in_timezone(latest_entry.timestamp_created, current_tz)
            print(f"Latest: {latest_time}")

        # Backup information
        _t = time.time()
        try:
            backups = list_backups()
            from tj.backup import get_backup_dir
            backup_dir = get_backup_dir()
            if backups:
                print(f"\nBackups ({len(backups)} files) [{backup_dir}]:")
                # Show latest 10 backups
                for backup in backups[:10]:
                    size_kb = backup['size'] / 1024
                    if size_kb < 1024:
                        size_str = f"{size_kb:.0f}K"
                    else:
                        size_mb = size_kb / 1024
                        size_str = f"{size_mb:.1f}M"
                    timestamp = backup['modified'].strftime('%Y-%m-%d %H:%M')
                    print(f"  {backup['filename']:<30} {size_str:>8}  {timestamp}")
            else:
                print(f"\nBackups: No backups found [{backup_dir}]")
        except Exception:
            traceback.print_exc()
        debug_time("list_backups", _t)

        # Telegram bot status
        from tj.state import get_tgbot_status
        tgbot = get_tgbot_status()
        exchanges = f", {tgbot['exchanges_24h']} exchanges in 24h"
        if tgbot['running']:
            if tgbot.get('remote'):
                age = tgbot['heartbeat_age']
                ago = f"{age}s" if age < 120 else f"{age // 60}m"
                print(f"\nTelegram bot: running (heartbeat {ago} ago){exchanges}")
            else:
                print(f"\nTelegram bot: running (PID {tgbot['pid']}){exchanges}")
        elif tgbot.get('remote'):
            age = tgbot['heartbeat_age']
            ago = f"{age // 60}m" if age < 7200 else f"{age // 3600}h"
            print(f"\nTelegram bot: not responding (last heartbeat {ago} ago)")
        elif tgbot.get('remote_error'):
            print(f"\nTelegram bot: unknown (server unreachable)")
        else:
            print(f"\nTelegram bot: not running")

        # Config info
        from tj.config import get_config_lines
        print("\nConfig:")
        for line in get_config_lines():
            print(line)

        print("\nTry: tj \"your memory here\", tj h for help")

    except Exception as e:
        traceback.print_exc()
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
        if not remaining_args:
            # No args: clear active context
            state = get_state()
            current_context = state.get("current_context")
            if current_context:
                state["current_context"] = None
                save_state(state)
                print(f"Context '{current_context}' cleared.")
            else:
                print("No context to clear.")
            return
        else:
            # Has args: create context-free entry
            input_list = ['=0'] + remaining_args
            class Args:
                def __init__(self):
                    self.input = input_list
                    self.timestamp_override = None
            args = Args()
            handle_creation(args)
            return

    # Extract context name (everything after =)
    context_name = first_arg[1:]  # Remove the = prefix

    if not context_name:
        # tj = with no name - show current context
        state = get_state()
        current = state.get("current_context")
        print(f"Current context: {current}" if current else "Current context: none")
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
        handle_creation_with_at(create_args)


def _handle_context_switch() -> bool:
    """Handle =context as first argument. Returns True if handled."""
    if len(sys.argv) <= 1 or not sys.argv[1].startswith('='):
        return False

    from tj.state import get_state, save_state
    from tj.commands.list import handle_list_command

    context_arg = sys.argv[1]
    remaining_args = sys.argv[2:]
    context_name = context_arg[1:]

    # Check for metadata flags (-t, -d) - delegate to full handler
    if '-t' in remaining_args or '-d' in remaining_args:
        handle_context_syntax(context_arg, remaining_args)
        return True

    state = get_state()
    context_just_created = False

    if context_name == '0':
        state["current_context"] = None
        save_state(state)
    elif context_name:
        from tj.repository import Context
        from datetime import datetime, timezone

        repository = RepositoryFactory.get_repository()
        if not repository.get_context(context_name):
            # Context doesn't exist - confirm creation
            if not confirm_action(f"Context '{context_name}' does not exist. Create it?"):
                print("Cancelled.")
                return True

            now = datetime.now(timezone.utc).timestamp()
            new_context = Context(
                name=context_name,
                title=None,
                description=None,
                timestamp_created=now,
                timestamp_modified=now
            )
            repository.create_context(new_context)
            print(f"Context '{context_name}' created.")
            context_just_created = True
        else:
            state["current_context"] = context_name
            save_state(state)

    # If no remaining args, list context entries (unless just created)
    if not remaining_args:
        if context_name and context_name != '0' and not context_just_created:
            class ListArgs:
                filters = [f'={context_name}']
            handle_list_command(ListArgs())
        return True

    return False


def _handle_numbered_command(parser) -> bool:
    """Handle numbered commands like '5 x'. Returns True if handled."""
    if len(sys.argv) <= 1:
        return False

    first_arg = sys.argv[1]

    # Check if first argument is a small number (avoid dates like YYYYMMDD)
    if not (first_arg.isdigit() and len(first_arg) <= 3):
        return False

    from tj.commands.list import handle_list_command

    if len(sys.argv) < 3:
        # No action specified - default to list (tj 5 → tj l 5)
        class Args:
            def __init__(self):
                self.filters = [first_arg]
        handle_list_command(Args())
        return True

    num_identifier = int(first_arg)
    action_command = sys.argv[2]
    remaining_args = sys.argv[3:]

    handle_numbered_command(parser, num_identifier, action_command, remaining_args)
    return True


def _handle_known_command(parser) -> bool:
    """Handle known subcommands. Returns True if handled."""
    if len(sys.argv) <= 1:
        return False

    first_arg = sys.argv[1]
    known_commands = set(parser._subparsers._group_actions[0].choices.keys())

    if first_arg not in known_commands:
        return False

    try:
        args = parser.parse_args()
        if hasattr(args, 'func'):
            args.func(args)
    except SystemExit:
        pass
    return True


def _handle_default_creation() -> None:
    """Handle default content creation when no command matches."""
    timestamp_override, filtered_input = parse_at_timestamp(sys.argv[1:])

    class Args:
        def __init__(self):
            self.input = filtered_input
            self.timestamp_override = timestamp_override

    handle_creation(Args())


def main() -> None:
    """Main function to parse arguments and dispatch commands."""
    from tj.state import display_context
    from io import StringIO

    time_main_start = time.time()
    output_buffer = StringIO()
    original_stdout = sys.stdout

    try:
        sys.stdout = output_buffer

        if _handle_context_switch():
            return

        parser = create_parser()

        if len(sys.argv) == 1:
            show_status()
            return

        if _handle_numbered_command(parser):
            return

        if _handle_known_command(parser):
            return

        _handle_default_creation()

    finally:
        sys.stdout = original_stdout
        debug_time("main_command", time_main_start)

        time_display = time.time()
        display_context()
        debug_time("display_context", time_display)

        time_output = time.time()
        buffered_output = output_buffer.getvalue()
        if buffered_output:
            print(buffered_output, end='')
        debug_time("print_output", time_output)

def _check_macos_venv() -> None:
    """On macOS, check if venv with requests exists. Print clear error if not."""
    import platform
    if platform.system() != "Darwin":
        return

    from pathlib import Path
    venv_python = Path.home() / ".tjai" / "venv" / "bin" / "python3"
    if not venv_python.exists():
        print("ERROR: macOS requires ~/.tjai/venv with 'requests' installed.", file=sys.stderr)
        print("Run: python3 -m venv ~/.tjai/venv && ~/.tjai/venv/bin/pip install requests", file=sys.stderr)
        sys.exit(1)

    # Check requests is importable from venv
    import subprocess
    result = subprocess.run(
        [str(venv_python), "-c", "import requests"],
        capture_output=True
    )
    if result.returncode != 0:
        print("ERROR: 'requests' not installed in ~/.tjai/venv", file=sys.stderr)
        print("Run: ~/.tjai/venv/bin/pip install requests", file=sys.stderr)
        sys.exit(1)


def _check_db_dir() -> None:
    """Ensure configured db_dir exists."""
    from pathlib import Path
    from tj.config import get_config

    config = get_config()
    db_dir = Path(config.get("db_dir", "~/Dropbox/Current")).expanduser()

    if not db_dir.exists():
        print(f"ERROR: db_dir '{db_dir}' not found.", file=sys.stderr)
        print("Set db_dir in ~/.tjai/config.json or create the directory.", file=sys.stderr)
        sys.exit(1)


def entrypoint() -> None:
    """Main entry point with error handling."""
    try:
        debug_mark("entrypoint_start")

        _check_macos_venv()
        _check_db_dir()

        # Options already parsed by tj.options module at import time
        # Set up sys.argv for command parsing (options stripped)
        sys.argv = [sys.argv[0]] + get_command_args()

        _t = time.time()
        init_db()
        debug_time("init_db", _t)

        # Auto-backup on every command execution (skip if using non-default db)
        _t = time.time()
        if not DB_PATH:
            auto_backup()
        debug_time("auto_backup", _t)

        # Ensure agent is running (only if not recently active)
        _t = time.time()
        _ensure_agent_running()
        debug_time("ensure_agent", _t)

        debug_mark("before_main")
        main()
    except DatabaseError as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
