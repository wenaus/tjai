"""Delete command handlers for tj."""

import sys

from tj.colors import colorize_context, colorize_creation_timestamp
from tj.commands.common import get_entry_from_recent_list, confirm_action, truncate_content
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


def handle_delete_new(args) -> None:
    """Handle delete operations with new patterns."""
    from tj.commands.common import extract_context_from_args
    try:
        if not args.args:
            print("Error: Please specify what to delete.", file=sys.stderr)
            print("Usage:")
            print("  tj d <number>           - Delete entry")
            print("  tj d <n1> <n2> <n3>...  - Delete multiple entries")
            print("  tj d <n1-n2>            - Delete range of entries")
            print("  tj d <number> t <tag>   - Delete tag from entry")
            print("  tj d t <tagname>        - Delete all instances of tag")
            print("  tj d =context           - Delete context (if empty)")
            return

        # Check for context deletion (tj d =contextname)
        if len(args.args) == 1 and args.args[0].startswith('='):
            context_name = args.args[0][1:]
            handle_delete_context(context_name)
            return

        # Extract context marker if present (e.g., tj d =context 5)
        # Do NOT set current context - just extract it for filtering
        if args.args and args.args[0].startswith('='):
            first_arg, args.args = extract_context_from_args(args.args[0], args.args[1:], set_context=False)
            if first_arg is None:
                # Just context marker, no delete operation
                return
            # Put first_arg back into args.args
            args.args = [first_arg] + list(args.args)

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

        # Handle single named entry deletion (e.g., tj d underway or tj d =context underway)
        if len(args.args) == 1 and not args.args[0].isdigit() and '-' not in args.args[0]:
            # It's a name, not a number - get_entry_from_recent_list handles context awareness
            entry = get_entry_from_recent_list(args.args[0])
            if not entry:
                print(f"Error: Entry '{args.args[0]}' not found.", file=sys.stderr)
                return
            handle_delete_single_entry(entry)
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
                print(f"Error: Invalid entry identifier '{arg}'.", file=sys.stderr)
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

        # Show what will be deleted and confirm
        if len(entries_to_delete) == 1:
            num, entry = entries_to_delete[0]
            time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))
            context_str = f" {colorize_context(entry.context)}" if entry.context else ""
            if not confirm_action(f"Delete: {time_str} {entry.content}{context_str}"):
                print("Delete cancelled.")
                return
        else:
            print(f"Delete {len(entries_to_delete)} entries:", file=sys.__stdout__, flush=True)
            for num, entry in entries_to_delete:
                time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))
                content_preview = truncate_content(entry.content)
                print(f"  {num}: {time_str} {content_preview}", file=sys.__stdout__, flush=True)
            if not confirm_action(f"\nDelete these {len(entries_to_delete)} entries?"):
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
        from tj.colors import colorize_content
        print(f"Remove tag '{tag}' from entry {entry_num}:", file=sys.__stdout__, flush=True)
        print(f"  {colorize_content(entry.content)}", file=sys.__stdout__, flush=True)

        if not confirm_action(f"\nRemove tag '{tag}'?"):
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
        print(f"Delete tag '{tagname}' from {tag_count} entries?", file=sys.__stdout__, flush=True)
        if not confirm_action("This will remove the tag from all entries. Continue?"):
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


def handle_delete_context(context_name: str) -> None:
    """Delete a context (only if it has no entries)."""
    try:
        repository = RepositoryFactory.get_repository()

        # Check if context exists
        context = repository.get_context(context_name)
        if not context:
            print(f"Error: Context '{context_name}' not found.", file=sys.stderr)
            return

        # Check for entries in this context
        entries = repository.query_entries(context=context_name)
        active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]

        if active_entries:
            from tj.colors import colorize_content
            print(f"Error: Cannot delete context '{context_name}' - it has {len(active_entries)} entries.", file=sys.stderr)
            print("\nEntries in this context:")
            for entry in active_entries[:10]:  # Show first 10
                time_str = colorize_creation_timestamp(format_time_dashboard(entry.timestamp_created))
                content_preview = truncate_content(entry.content)
                print(f"  {time_str} {colorize_content(content_preview)}")
            if len(active_entries) > 10:
                print(f"  ... and {len(active_entries) - 10} more")
            return

        # Confirm deletion
        if not confirm_action(f"Delete context '{context_name}'?"):
            print("Context deletion cancelled.")
            return

        # Delete the context
        success = repository.delete_context(context_name)
        if success:
            print(f"Context '{context_name}' deleted.")
        else:
            print("Error: Failed to delete context.", file=sys.stderr)

    except Exception as e:
        print(f"Delete context error: {e}", file=sys.stderr)
