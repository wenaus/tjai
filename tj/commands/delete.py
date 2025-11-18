"""Delete command handlers for tj."""

import sys

from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


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
