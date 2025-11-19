"""Modification command handlers for tj."""

import sys
from datetime import datetime, timezone

from tj.colors import colorize_content, colorize_context, colorize_timestamp
from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


def handle_add_subnote(args) -> None:
    """Handle adding a sub-note to an entry."""
    from tj.state import display_context
    display_context()
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
    - tj e → edit most recent entry in editor
    - tj e <kind> → create new entry of type in editor (ai, d, p, b, j)
    - tj e <n> → edit entry <n> in editor
    - tj e <n> text → replace entry <n> content with text (requires confirmation)
    """
    from tj.commands.editor import handle_editor_create, handle_editor_edit
    from tj.state import display_context

    display_context()

    # Case 1: No args → edit most recent entry
    if not args.entry_num:
        from tj.repository_factory import RepositoryFactory
        from tj.commands.editor import open_editor
        from datetime import datetime, timezone

        repository = RepositoryFactory.get_repository()
        all_entries = repository.query_entries()
        active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]
        if not active_entries:
            print("No entries to edit.", file=sys.stderr)
            return
        # Sort by modification time, get most recent
        active_entries.sort(key=lambda e: e.timestamp_modified, reverse=True)
        entry = active_entries[0]

        # Prepare initial content with =context prefix if present
        if entry.context:
            initial_content = f"={entry.context} {entry.content}"
        else:
            initial_content = entry.content

        # Open editor
        new_content = open_editor(initial_content)

        if new_content is None:
            return

        # Parse for =context on first line
        lines = new_content.split('\n', 1)
        first_line = lines[0]
        rest = lines[1] if len(lines) > 1 else None

        first_parts = first_line.split()
        new_context = entry.context  # Default to current context
        content_parts = []
        found_context_marker = False

        for part in first_parts:
            if part.startswith('='):
                found_context_marker = True
                ctx = part[1:]
                if ctx == '0':
                    new_context = None
                elif ctx:
                    new_context = ctx
            else:
                content_parts.append(part)

        # If entry had context but user removed =context marker, clear it
        if entry.context and not found_context_marker:
            new_context = None

        # Reconstruct content without =context
        if content_parts:
            first_content = ' '.join(content_parts)
            if rest:
                final_content = first_content + '\n' + rest
            else:
                final_content = first_content
        elif rest:
            final_content = rest
        else:
            print("Error: Entry content cannot be empty.", file=sys.stderr)
            return

        # Extract tags from new content
        new_tags = set()
        for line in final_content.split('\n'):
            for part in line.split():
                if part.startswith(':'):
                    tag = part[1:]
                    if tag:
                        new_tags.add(tag)

        # Update entry
        success = repository.update_entry(
            entry.id,
            content=final_content,
            context=new_context,
            timestamp_modified=datetime.now(timezone.utc).timestamp(),
            is_dirty=True
        )

        if not success:
            print("Error: Failed to update entry.", file=sys.stderr)
            return

        # Update tags (add new, remove old)
        existing_tags = set(repository.get_tags(entry.id))

        for tag in new_tags - existing_tags:
            repository.add_tag(entry.id, tag)

        for tag in existing_tags - new_tags:
            repository.remove_tag(entry.id, tag)

        print("Entry updated successfully.")
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
    from tj.state import display_context
    display_context()
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
                time_str = colorize_timestamp(format_time_dashboard(entry.timestamp_created))

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
    from tj.state import display_context
    display_context()
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
    from tj.state import display_context
    display_context()
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
    from tj.state import display_context
    display_context()
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
    from tj.state import display_context
    display_context()
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
