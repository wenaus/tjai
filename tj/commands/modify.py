"""Modification command handlers for tj."""

import sys
from datetime import datetime, timezone

from tj.colors import colorize_content, colorize_context
from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


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
    """Handle editing an entry.

    Modes:
    - tj e → create new memory in editor
    - tj e <kind> → create new entry of type in editor (ai, d, p, b, j)
    - tj e <n> → edit entry <n> in editor
    - tj e <n> text → replace entry <n> content with text (requires confirmation)
    """
    from tj.commands.editor import handle_editor_create, handle_editor_edit

    # Case 1: No args → create memory in editor
    if not args.entry_num:
        handle_editor_create()
        return

    # Check if entry_num is a kind type
    kind_map = {
        'ai': 'ai',
        'd': 'todo',
        'todo': 'todo',
        'p': 'profile',
        'profile': 'profile',
        'b': 'bookmark',
        'bookmark': 'bookmark',
        'j': 'calendar',
        'calendar': 'calendar'
    }

    if args.entry_num in kind_map:
        # Create entry of specified type in editor
        entry_type = kind_map[args.entry_num]
        # Text becomes context/tags
        handle_editor_create(entry_type=entry_type, extra_args=args.text if args.text else [])
        return

    # Try to parse entry_num as integer
    try:
        entry_num = int(args.entry_num)
    except (ValueError, TypeError):
        print("Error: First argument must be an entry number or type (ai, d, p, b, j).", file=sys.stderr)
        return

    # Case 2: Entry number but no text → edit in editor
    if not args.text:
        handle_editor_edit(entry_num)
        return

    # Case 3: Entry number + text → command-line edit with confirmation
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

            for i, entry in enumerate(sorted_entries, 1):
                content_colored = colorize_content(entry.content)
                time_str = format_time_dashboard(entry.timestamp_created)

                context_str = f" {colorize_context(entry.context)}" if entry.context else ""
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

        # Strip leading = if present (support both "tj m 4 ctx" and "tj m 4 =ctx")
        if context and context.startswith('='):
            context = context[1:]

        # Treat "0" as "clear context"
        if context == "0":
            context = None

        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return

        repository = RepositoryFactory.get_repository()
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
                print(f"Entry {entry_num} context cleared.")
        else:
            print("Error: Failed to move entry.", file=sys.stderr)

    except (ValueError, TypeError):
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        print(f"Move error: {e}", file=sys.stderr)


def handle_show(args) -> None:
    """Handle showing entry details."""
    try:
        entry_identifier = args.entry_num  # Can be number or @name

        entry = get_entry_from_recent_list(entry_identifier)
        if not entry:
            print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
            return

        time_str = format_time_dashboard(entry.timestamp_created)

        # Display identifier (name or number)
        display_id = f"@{entry.name}" if entry.name else entry_identifier
        print(f"Entry {display_id}:")
        print(f"  Content: {entry.content}")
        print(f"  Created: {time_str}")
        print(f"  Type: {entry.kind}")
        if entry.context:
            print(f"  Context: {entry.context}")
        if entry.name:
            print(f"  Name: @{entry.name}")
        if entry.priority is not None:
            print(f"  Priority: {entry.priority}")
        if entry.status:
            print(f"  Status: {entry.status}")

        # Show tags
        repository = RepositoryFactory.get_repository()
        tags = repository.get_tags(entry.id)
        if tags:
            print(f"  Tags: {', '.join(tags)}")

        # Show links if present
        if entry.data and 'links' in entry.data:
            links = entry.data['links']
            if links:
                print("  Links:")
                for link in links:
                    title = link.get('title', 'Link')
                    url = link.get('url', '')
                    # Color URL cyan
                    colored_url = f"\033[96m{url}\033[0m"
                    print(f"    {title}: {colored_url}")

        # Set as last parent for sub-items (if not a sub-item itself)
        if not entry.parent_id:
            from tj.state import set_last_parent
            set_last_parent(entry.id)

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
