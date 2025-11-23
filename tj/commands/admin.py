"""Admin commands for tj database maintenance."""

import sys


def handle_backup() -> None:
    """Create a manual backup."""
    from tj.backup import create_backup

    try:
        success = create_backup()
        if success:
            print("Backup created successfully.")
        else:
            print("Backup failed.", file=sys.stderr)
    except Exception as e:
        print(f"Backup error: {e}", file=sys.stderr)


def handle_admin(args) -> None:
    """Handle admin commands."""
    from tj.state import display_context

    # Check if subcommand provided
    if not hasattr(args, 'subcommand') or not args.subcommand:
        # List available admin commands
        print("Admin commands:")
        print("  tj admin backup   Create a manual backup")
        print("  tj admin purge    Permanently delete soft-deleted entries")
        return

    subcommand = args.subcommand

    if subcommand == 'backup':
        handle_backup()
    elif subcommand == 'purge':
        handle_purge()
    else:
        # Unknown admin command
        print(f"Error: 'admin' is reserved for admin commands.", file=sys.stderr)
        print(f"Unknown admin command: '{subcommand}'", file=sys.stderr)
        print("Use 'tj admin' to see available admin commands.", file=sys.stderr)


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
            content_preview = entry.content[:60] + "..." if len(entry.content) > 60 else entry.content
            context_str = f" ={entry.context}" if entry.context else ""
            print(f"  {i}. [{entry.kind}]{context_str} {colorize_content(content_preview)} (deleted {time_str})")

        if len(deleted_entries) > 5:
            print(f"  ... and {len(deleted_entries) - 5} more")

        # Confirm
        print(f"\nPermanently delete {len(deleted_entries)} soft-deleted entries? [y/N]: ", end='', file=sys.__stdout__, flush=True)
        response = input().strip().lower()
        if response not in ['y', 'yes']:
            print("Purge cancelled.")
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
        conn.close()

        # Get database size after
        size_after = os.path.getsize(db_path)
        size_reclaimed = size_before - size_after
        size_reclaimed_kb = size_reclaimed / 1024

        print(f"Purged {entries_deleted} entries, {tags_deleted} orphaned tags")
        print(f"Reclaimed {size_reclaimed_kb:.1f} KB")

    except Exception as e:
        print(f"Purge error: {e}", file=sys.stderr)
