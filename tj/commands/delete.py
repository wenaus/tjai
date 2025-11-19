"""Delete command handlers for tj."""

import sys

from tj.colors import colorize_context, colorize_creation_timestamp
from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


def handle_delete_new(args) -> None:
    """Handle delete operations with new patterns."""
    try:
        if not args.args:
            print("Error: Please specify what to delete.", file=sys.stderr)
            print("Usage:")
            print("  tj x <number>           - Delete entry")
            print("  tj x <n1> <n2> <n3>...  - Delete multiple entries")
            print("  tj x <n1-n2>            - Delete range of entries")
            print("  tj x <number> t <tag>   - Delete tag from entry")
            print("  tj x t <tagname>        - Delete all instances of tag")
            return

        # Check for tag operations first
        if len(args.args) == 2 and args.args[0] == 't':
            # tj x t <tagname> - delete all tag instances
            tagname = args.args[1]
            handle_delete_all_tag_instances(tagname)
            return

        if len(args.args) == 3 and args.args[1] == 't':
            # tj x <number> t <tag> - delete tag from entry
            if args.args[0].isdigit():
                entry_num = int(args.args[0])
                tag = args.args[2]
                handle_delete_tag_from_entry(entry_num, tag)
                return
            else:
                print(f"Error: Invalid entry number '{args.args[0]}'.", file=sys.stderr)
                return

        # Parse entry numbers - could be single, multiple, or range
        entry_nums = []
        for arg in args.args:
            if '-' in arg and not arg.startswith('-'):
                # Range: 1-10
                parts = arg.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    start = int(parts[0])
                    end = int(parts[1])
                    if start <= end:
                        entry_nums.extend(range(start, end + 1))
                    else:
                        print(f"Error: Invalid range '{arg}' (start must be <= end).", file=sys.stderr)
                        return
                else:
                    print(f"Error: Invalid range format '{arg}'.", file=sys.stderr)
                    return
            elif arg.isdigit():
                entry_nums.append(int(arg))
            else:
                print(f"Error: Invalid entry number '{arg}'.", file=sys.stderr)
                return

        if entry_nums:
            handle_delete_entries(entry_nums)
        else:
            print("Error: No valid entry numbers specified.", file=sys.stderr)

    except Exception as e:
        print(f"Delete error: {e}", file=sys.stderr)


def handle_delete_entries(entry_nums: list) -> None:
    """Delete multiple entries (requires confirmation)."""
    try:
        repository = RepositoryFactory.get_repository()

        # Get all entries and validate they exist
        entries_to_delete = []
        for num in entry_nums:
            entry = get_entry_from_recent_list(num)
            if not entry:
                print(f"Error: Entry {num} not found in recent list.", file=sys.stderr)
                return
            entries_to_delete.append((num, entry))

        # Show what will be deleted
        if len(entries_to_delete) == 1:
            num, entry = entries_to_delete[0]
            time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))
            context_str = f" {colorize_context(entry.context)}" if entry.context else ""
            response = input(f"Delete: {time_str} {entry.content}{context_str} [y/N]: ").strip().lower()
        else:
            print(f"Delete {len(entries_to_delete)} entries:")
            for num, entry in entries_to_delete:
                time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))
                content_preview = entry.content[:60] + "..." if len(entry.content) > 60 else entry.content
                print(f"  {num}: {time_str} {content_preview}")
            response = input(f"\nDelete these {len(entries_to_delete)} entries? [y/N]: ").strip().lower()

        if response not in ['y', 'yes']:
            print("Delete cancelled.")
            return

        # Perform deletions
        deleted_count = 0
        for num, entry in entries_to_delete:
            if repository.delete_entry(entry.id):
                deleted_count += 1

        if deleted_count == len(entries_to_delete):
            print(f"Deleted {deleted_count} entries successfully.")
        else:
            print(f"Deleted {deleted_count} of {len(entries_to_delete)} entries.", file=sys.stderr)

    except Exception as e:
        print(f"Delete entries error: {e}", file=sys.stderr)


def handle_delete_entry(entry_num: int) -> None:
    """Delete an entry (requires confirmation)."""
    handle_delete_entries([entry_num])


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
