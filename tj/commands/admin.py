"""Admin commands for tj database maintenance."""

import sys
import traceback
from datetime import datetime, timezone

from tj.commands.common import log_operation, confirm_action, truncate_content


def handle_backup() -> None:
    """Create a manual backup."""
    from tj.backup import create_backup, get_backup_dir
    from tj.config import get_location_name
    import os

    try:
        success, error_msg = create_backup()
        if success:
            print("Backup created successfully.")

            # Get backup file size
            backup_dir = get_backup_dir()
            location_name = get_location_name()
            datetime_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
            backup_filename = f"tjai_{location_name}_{datetime_str}.db"
            backup_path = backup_dir / backup_filename

            if backup_path.exists():
                size_bytes = os.path.getsize(backup_path)
                size_kb = round(size_bytes / 1024, 1)
                log_operation('backup', 'success', {'size_kb': size_kb})
            else:
                log_operation('backup', 'success')
        else:
            print("Backup failed.", file=sys.stderr)
            log_operation('backup', 'error', {'error': error_msg})
    except Exception as e:
        traceback.print_exc()
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"Backup error: {error_msg}", file=sys.stderr)
        log_operation('backup', 'error', {'error': error_msg})


def handle_admin(args) -> None:
    """Handle admin commands."""
    from tj.state import display_context, get_state

    # Check if subcommand provided
    if not hasattr(args, 'subcommand') or not args.subcommand:
        # Show safe mode status if active
        state = get_state()
        if state.get("safe_mode"):
            print("⚠ Safe mode active")

        # List available admin commands
        print("Admin commands:")
        print("  tj admin backup         Create a manual backup")
        print("  tj admin purge          Permanently delete soft-deleted entries")
        print("  tj admin safe           Enable safe mode")
        print("  tj admin normal         Disable safe mode")
        print("  tj admin lines N        Set content truncation to N lines")
        print("  tj admin agent          Show agent status")
        print("  tj admin agent start    Start sync agent")
        print("  tj admin agent stop     Stop sync agent")
        print("  tj admin agent restart  Restart sync agent")
        print("  tj admin agent log      Show agent log")
        print("  tj admin agent install  Install agent service manually")
        return

    subcommand = args.subcommand

    if subcommand == 'backup':
        handle_backup()
    elif subcommand == 'purge':
        handle_purge()
    elif subcommand == 'safe':
        handle_safe()
    elif subcommand == 'normal':
        handle_normal()
    elif subcommand == 'lines':
        handle_lines(args)
    elif subcommand == 'agent':
        from tj.commands.agent import handle_agent
        handle_agent(args)
    else:
        # Unknown admin command
        print(f"Error: 'admin' is reserved for admin commands.", file=sys.stderr)
        print(f"Unknown admin command: '{subcommand}'", file=sys.stderr)
        print("Use 'tj admin' to see available admin commands.", file=sys.stderr)


def handle_lines(args) -> None:
    """Set content truncation line count."""
    from tj.config import get_config, save_config

    # Check if N provided
    if not hasattr(args, 'args') or not args.args:
        # Show current value
        config = get_config()
        current = config.get('content_truncate_length', 5)
        print(f"Content truncation: {current} lines")
        return

    # Parse N
    try:
        line_count = int(args.args[0])
        if line_count < 0:
            print("Error: Line count must be non-negative.", file=sys.stderr)
            return
    except ValueError:
        print(f"Error: Invalid line count '{args.args[0]}'", file=sys.stderr)
        return

    # Update config
    config = get_config()
    old_value = config.get('content_truncate_length')  # Actual value, no default
    config['content_truncate_length'] = line_count
    save_config(config)
    print(f"Content truncation set to {line_count} lines.")
    log_operation('lines', 'success', {'old_value': old_value, 'new_value': line_count})


def handle_safe() -> None:
    """Enable safe mode."""
    from tj.state import get_state, update_state

    if get_state().get("safe_mode"):
        print("Safe mode already active.")
        return

    update_state(safe_mode=True)
    print("Safe mode enabled.")
    log_operation('safe', 'success')


def handle_normal() -> None:
    """Disable safe mode."""
    from tj.state import get_state, update_state

    if not get_state().get("safe_mode"):
        print("Safe mode not active.")
        return

    update_state(safe_mode=False)
    print("Safe mode disabled.")
    log_operation('normal', 'success')


def handle_purge() -> None:
    """Permanently delete all soft-deleted entries."""
    from tj.repository_factory import RepositoryFactory
    from tj.database import get_db_connection
    from tj.colors import colorize_content
    from tj.timezone_manager import format_time_dashboard

    try:
        repository = RepositoryFactory.get_repository()

        # Get all entries
        all_entries = repository.query_entries()
        deleted_entries = [e for e in all_entries if getattr(e, 'deleted_at', None)]

        if not deleted_entries:
            print("No soft-deleted entries to purge.")
            return

        # Show count and samples
        print(f"Found {len(deleted_entries)} soft-deleted entries.")
        print("\nSample (first 5):")
        for i, entry in enumerate(deleted_entries[:5], 1):
            time_str = format_time_dashboard(entry.deleted_at)
            content_preview = truncate_content(entry.content)
            context_str = f" ={entry.context}" if entry.context else ""
            print(f"  {i}. [{entry.kind}]{context_str} {colorize_content(content_preview)} (deleted {time_str})")

        if len(deleted_entries) > 5:
            print(f"  ... and {len(deleted_entries) - 5} more")

        # Confirm
        if not confirm_action(f"\nPermanently delete {len(deleted_entries)} soft-deleted entries?"):
            print("Purge cancelled.")
            log_operation('purge', 'cancelled', {'entries_found': len(deleted_entries)})
            return

        # Get database size before
        import os
        from tj.config import get_db_path
        db_path = get_db_path()
        size_before = os.path.getsize(db_path)

        # Purge deleted entries
        conn = get_db_connection()
        cursor = conn.cursor()

        # Delete entries
        cursor.execute("DELETE FROM entries WHERE deleted_at IS NOT NULL")
        entries_deleted = cursor.rowcount

        # Delete orphaned tags
        cursor.execute("DELETE FROM tags WHERE entry_id NOT IN (SELECT id FROM entries)")
        tags_deleted = cursor.rowcount

        conn.commit()

        # Vacuum to reclaim space
        cursor.execute("VACUUM")

        # Get database size after
        size_after = os.path.getsize(db_path)
        size_reclaimed = size_before - size_after
        size_reclaimed_kb = size_reclaimed / 1024

        print(f"Purged {entries_deleted} entries, {tags_deleted} orphaned tags")
        print(f"Reclaimed {size_reclaimed_kb:.1f} KB")

        log_operation('purge', 'success', {
            'entries_deleted': entries_deleted,
            'tags_deleted': tags_deleted,
            'space_reclaimed_kb': round(size_reclaimed_kb, 1)
        })

    except Exception as e:
        traceback.print_exc()
        print(f"Purge error: {e}", file=sys.stderr)
        log_operation('purge', 'error', {'error': str(e)})
