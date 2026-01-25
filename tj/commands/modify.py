"""Modification command handlers for tj."""

import sys
import traceback
from datetime import datetime, timezone

from tj.colors import colorize_content, colorize_context, colorize_timestamp, colorize_entry_number
from tj.commands.common import get_entry_from_recent_list, confirm_action
from tj.config import get_preview_length, get_line_wrap_width
from tj.repository_factory import RepositoryFactory
from tj.timezone_manager import format_time_dashboard


def _parse_metadata_from_content(content: str, entry):
    """Extract metadata from content and return update fields.

    Args:
        content: The content string to parse
        entry: The existing entry (for defaults)

    Returns:
        Tuple of (cleaned_content, update_fields_dict, new_tags_set)
    """
    import re

    # Extract @name (must be at start of content)
    entry_name = entry.name  # Keep existing if not specified
    name_pattern = re.compile(r'^@([a-zA-Z][a-zA-Z0-9_-]*)')
    name_match = name_pattern.search(content)
    if name_match:
        entry_name = name_match.group(1)
        content = name_pattern.sub('', content, count=1)

    # Extract p=N (priority)
    entry_priority = entry.priority  # Keep existing if not specified
    priority_pattern = re.compile(r'\bp=(\d+)\b')
    priority_match = priority_pattern.search(content)
    if priority_match:
        entry_priority = int(priority_match.group(1))
        content = priority_pattern.sub('', content)

    # Extract s=value (status)
    entry_status = entry.status  # Keep existing if not specified
    status_pattern = re.compile(r'\bs=(\w+)\b')
    status_match = status_pattern.search(content)
    if status_match:
        entry_status = status_match.group(1)
        content = status_pattern.sub('', content)

    # Extract tags (must start with alpha character)
    from tj.commands.common import is_valid_tag
    new_tags = set()
    for line in content.split('\n'):
        for part in line.split():
            if part.startswith(':'):
                tag = part[1:]
                if is_valid_tag(tag):
                    new_tags.add(tag)

    # For journal entries with event_date, re-parse event date from content if a date is present
    entry_data = entry.data
    if entry.kind == 'journal':
        from tj.commands.journal import parse_date_spec
        from tj.timezone_manager import get_current_timezone
        from zoneinfo import ZoneInfo
        from datetime import datetime, time as dt_time

        # Get original event date
        original_event_ts = entry.data.get('event_date') if entry.data else None

        # Try to parse date from content
        content_words = content.strip().split()
        event_timestamp, remaining_words = parse_date_spec(content_words)

        # Only update event_date if a date/time was actually found in the content
        # If no date found, remaining_words will be same as content_words (nothing consumed)
        date_was_found = (remaining_words != content_words)

        if date_was_found:
            # Check if only time was specified (no date)
            # If first word is time-only format (not date/time combo), preserve original date
            first_word = content_words[0] if content_words else ""
            # Time-only if it has : but no / (excludes MMDD/HH:MM, mm/dd/HH:MM formats)
            is_time_only = ('/' not in first_word) and (':' in first_word or first_word.lower().endswith(('am', 'pm')))

            if is_time_only and original_event_ts:
                # Get timezone
                tz_name = get_current_timezone()
                try:
                    tz = ZoneInfo(tz_name)
                    original_dt = datetime.fromtimestamp(original_event_ts, tz=tz)
                    new_dt = datetime.fromtimestamp(event_timestamp, tz=tz)
                except Exception:
                    traceback.print_exc()
                    original_dt = datetime.fromtimestamp(original_event_ts)
                    new_dt = datetime.fromtimestamp(event_timestamp)

                # Combine original date with new time
                try:
                    if tz:
                        combined_dt = datetime.combine(original_dt.date(), dt_time(new_dt.hour, new_dt.minute, tzinfo=tz))
                    else:
                        combined_dt = datetime.combine(original_dt.date(), dt_time(new_dt.hour, new_dt.minute))
                    event_timestamp = combined_dt.timestamp()
                except Exception:
                    traceback.print_exc()
                    pass  # Fall back to parsed timestamp if combination fails

            # Rebuild content without the date/time prefix
            content = ' '.join(remaining_words) if remaining_words else content
            # Update entry.data with new event_date - MUST COPY to trigger DB update
            entry_data = dict(entry.data) if entry.data else {}
            entry_data['event_date'] = event_timestamp
        # else: preserve original event_date, keep content as-is

    update_fields = {
        'name': entry_name,
        'priority': entry_priority,
        'status': entry_status,
        'data': entry_data
    }

    return content.strip(), update_fields, new_tags


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
        preview_len = get_preview_length()
        print(f"Sub-note added to entry {entry_num}: {text[:preview_len]}...")

    except (ValueError, TypeError):
        traceback.print_exc()
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        traceback.print_exc()
        print(f"Add sub-note error: {e}", file=sys.stderr)


def _edit_entry_in_editor(entry, entry_identifier="entry", keep_time=False):
    """Helper function to edit an entry in the editor.

    Args:
        entry: The Entry object to edit
        entry_identifier: String identifier for messages (e.g., "@name" or "entry")
        keep_time: If True, preserve original modification time

    Returns:
        bool: True if edit was successful, False otherwise
    """
    from tj.commands.editor import open_editor

    # Prepare initial content with =context prefix if present
    if entry.context:
        initial_content = f"={entry.context} {entry.content}"
    else:
        initial_content = entry.content

    # Use @name if available, otherwise first 12-20 chars of content
    filename_hint = entry.name if entry.name else entry.content[:20]

    # Show what's being edited (print to original stdout to bypass buffer)
    import sys
    content_preview = entry.content.split('\n')[0][:60]
    if len(entry.content) > 60 or '\n' in entry.content:
        content_preview += "..."
    if entry.name:
        print(f"Editing @{entry.name}: {content_preview}", file=sys.__stdout__, flush=True)
    else:
        print(f"Editing: {content_preview}", file=sys.__stdout__, flush=True)

    # Open editor with entry kind and filename hint
    new_content = open_editor(initial_content, entry_type=entry.kind, filename_hint=filename_hint)
    if new_content is None:
        return False

    # Parse for =context on first line (only first =text is treated as context)
    from tj.commands.common import extract_first_context_from_parts

    lines = new_content.split('\n', 1)
    first_line = lines[0]
    rest = lines[1] if len(lines) > 1 else None

    first_parts = first_line.split()
    new_context, content_parts, found_context_marker = extract_first_context_from_parts(first_parts)

    # If context was found in content, use it; otherwise keep entry's existing context
    if found_context_marker:
        # Explicit context in edited content (including =0 to clear)
        pass  # new_context already set by extract function
    elif entry.context:
        # Entry had context but user removed =context marker, clear it
        new_context = None
    else:
        # Entry had no context, keep it that way
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
        return False

    # Parse metadata from edited content
    cleaned_content, metadata_fields, new_tags = _parse_metadata_from_content(final_content, entry)

    # Build update fields
    update_fields = {
        'content': cleaned_content,
        'context': new_context,
        'timestamp_modified': entry.timestamp_modified if keep_time else datetime.now(timezone.utc).timestamp(),
        'is_dirty': True
    }
    update_fields.update(metadata_fields)

    # Update entry
    repository = RepositoryFactory.get_repository()
    success = repository.update_entry(entry.id, **update_fields)

    if not success:
        print("Error: Failed to update entry.", file=sys.stderr)
        return False

    # Update tags (add new, remove old)
    existing_tags = set(repository.get_tags(entry.id))

    for tag in new_tags - existing_tags:
        repository.add_tag(entry.id, tag)

    for tag in existing_tags - new_tags:
        repository.remove_tag(entry.id, tag)

    print("Entry updated successfully.")

    # Show the updated entry
    updated_entry = repository.get_entry(entry.id)
    if updated_entry:
        print()
        display_entry_details(updated_entry, entry_identifier)

    return True


def handle_edit(args) -> None:
    """Handle editing an entry.

    Modes:
    - tj e → edit most recent entry in editor
    - tj e <kind> → create new entry of type in editor (ai, d, p, b, j)
    - tj e <n|@name> → edit entry <n> or @name in editor
    - tj e <n|@name> text → replace entry content with text (requires confirmation)
    """
    from tj.commands.editor import handle_editor_create
    from tj.state import display_context
    from tj.commands.common import extract_context_from_args

    # Extract context marker if present (e.g., tj e =context underway) - do NOT set current context
    if args.entry_num:
        args.entry_num, args.text = extract_context_from_args(args.entry_num, args.text if args.text else [], set_context=False)

    # Case 1: No args → create new entry
    if not args.entry_num:
        handle_editor_create(entry_type='memory', extra_args=args.text if args.text else [])
        return

    # Check if entry_num is a kind type
    from tj.commands.common import ENTRY_TYPE_MAP

    if args.entry_num in ENTRY_TYPE_MAP:
        # Create entry of specified type in editor
        entry_type = ENTRY_TYPE_MAP[args.entry_num]
        # Text becomes context/tags
        handle_editor_create(entry_type=entry_type, extra_args=args.text if args.text else [])
        return

    # Try to parse entry_num as integer first
    try:
        entry_num = int(args.entry_num)

        # Special case: 0 means edit most recent entry
        if entry_num == 0:
            repository = RepositoryFactory.get_repository()
            all_entries = repository.query_entries()
            active_entries = [e for e in all_entries if not getattr(e, 'deleted_at', None)]
            if not active_entries:
                print("No entries to edit.", file=sys.stderr)
                return
            # Sort by modification time, get most recent
            active_entries.sort(key=lambda e: e.timestamp_modified, reverse=True)
            entry = active_entries[0]
            keep_time = getattr(args, 'keep_time', False)
            _edit_entry_in_editor(entry, "most recent entry", keep_time=keep_time)
            return

    except (ValueError, TypeError):
        # Not a number, check if it's a name reference (with or without @)
        if isinstance(args.entry_num, str):
            # Normalize to @name format for lookup
            name_ref = args.entry_num if args.entry_num.startswith('@') else f"@{args.entry_num}"

            # Look up the named entry
            entry = get_entry_from_recent_list(name_ref)
            if not entry:
                # Entry not found - confirm before creating
                name_without_at = name_ref[1:] if name_ref.startswith('@') else name_ref
                if not confirm_action(f"Entry @{name_without_at} does not exist. Create it?"):
                    print("Cancelled.")
                    return

                # Build extra_args: @name plus any additional args like :tags
                extra_args = [f'@{name_without_at}']
                if args.text:
                    extra_args.extend(args.text)
                # Open editor to create new entry
                handle_editor_create(entry_type='memory', extra_args=extra_args)
                return

            # If there's text, do command-line edit with confirmation
            if args.text:
                from tj.commands.common import extract_first_context_from_parts

                # Extract inline =context from text
                new_context, filtered_text, found_context = extract_first_context_from_parts(args.text)
                new_text = " ".join(filtered_text)

                # Determine final context
                if found_context:
                    # Use the extracted context (could be None if =0)
                    final_context = new_context
                else:
                    # No context specified, keep existing
                    final_context = entry.context

                # If only context was provided (no content), keep existing content
                if found_context and not new_text:
                    new_text = entry.content

                # Show changes and confirm
                print(f"Edit entry {name_ref}:", file=sys.__stdout__, flush=True)
                print(f"  Old: {entry.content}", file=sys.__stdout__, flush=True)
                print(f"  New: {new_text}", file=sys.__stdout__, flush=True)

                if not confirm_action("\nConfirm edit?"):
                    print("Edit cancelled.")
                    return

                # Parse metadata from new content
                cleaned_content, metadata_fields, new_tags = _parse_metadata_from_content(new_text, entry)

                # Build update fields
                update_fields = {
                    'content': cleaned_content,
                    'context': final_context,
                    'timestamp_modified': datetime.now(timezone.utc).timestamp(),
                    'is_dirty': True
                }
                update_fields.update(metadata_fields)

                repository = RepositoryFactory.get_repository()
                success = repository.update_entry(entry.id, **update_fields)

                if success:
                    # Update tags
                    existing_tags = set(repository.get_tags(entry.id))
                    for tag in new_tags - existing_tags:
                        repository.add_tag(entry.id, tag)
                    for tag in existing_tags - new_tags:
                        repository.remove_tag(entry.id, tag)
                    print("Entry updated successfully.")
                else:
                    print("Error: Failed to update entry.", file=sys.stderr)
                return

            # No text - edit in editor using helper
            keep_time = getattr(args, 'keep_time', False)
            _edit_entry_in_editor(entry, name_ref, keep_time=keep_time)
            return
        else:
            print("Error: First argument must be an entry number, name, or type (ai, do, p, b, j).", file=sys.stderr)
            return

    # Case 2: Entry number but no text → edit in editor
    if not args.text:
        entry = get_entry_from_recent_list(entry_num)
        if not entry:
            print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
            return
        keep_time = getattr(args, 'keep_time', False)
        _edit_entry_in_editor(entry, str(entry_num), keep_time=keep_time)
        return

    # Case 3: Entry number + text → command-line edit with confirmation
    from tj.commands.common import extract_first_context_from_parts

    # Extract inline =context from text
    new_context, filtered_text, found_context = extract_first_context_from_parts(args.text)
    new_text = " ".join(filtered_text)

    entry = get_entry_from_recent_list(entry_num)
    if not entry:
        print(f"Error: Entry {entry_num} not found in recent list.", file=sys.stderr)
        return

    # Determine final context
    if found_context:
        # Use the extracted context (could be None if =0)
        final_context = new_context
    else:
        # No context specified, keep existing
        final_context = entry.context

    # If only context was provided (no content), keep existing content
    if found_context and not new_text:
        new_text = entry.content

    # Show current content and confirm
    print(f"Edit entry {entry_num}:", file=sys.__stdout__, flush=True)
    print(f"  Old: {entry.content}", file=sys.__stdout__, flush=True)
    print(f"  New: {new_text}", file=sys.__stdout__, flush=True)

    if not confirm_action("\nConfirm edit?"):
        print("Edit cancelled.")
        return

    # Parse metadata from new content
    cleaned_content, metadata_fields, new_tags = _parse_metadata_from_content(new_text, entry)

    # Build update fields
    update_fields = {
        'content': cleaned_content,
        'context': final_context,
        'timestamp_modified': datetime.now(timezone.utc).timestamp(),
        'is_dirty': True
    }
    update_fields.update(metadata_fields)

    # Update entry
    repository = RepositoryFactory.get_repository()
    success = repository.update_entry(entry.id, **update_fields)

    if success:
        # Update tags
        existing_tags = set(repository.get_tags(entry.id))
        for tag in new_tags - existing_tags:
            repository.add_tag(entry.id, tag)
        for tag in existing_tags - new_tags:
            repository.remove_tag(entry.id, tag)
        print("Entry updated successfully.")
    else:
        print("Error: Failed to update entry.", file=sys.stderr)


def handle_tag_command(args) -> None:
    """Handle tag command - either add tag to entry or list entries with tag."""
    try:
        if args.tag:
            # Two arguments: tj t <number|name> <tag> - add tag to entry
            entry_identifier = args.entry_num  # Can be number or name
            tag = args.tag.strip()

            entry = get_entry_from_recent_list(entry_identifier)
            if not entry:
                print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
                return

            repository = RepositoryFactory.get_repository()
            repository.add_tag(entry.id, tag)
            print(f"Tag '{tag}' added to entry {entry_identifier}.")

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
                    print(f"{colorize_entry_number(i)}  {time_str} ToDo: {content_colored}{context_str}")
                elif entry.kind in ['memory', 'bookmark']:
                    print(f"{colorize_entry_number(i)}  {time_str} {content_colored}{context_str}")
                else:
                    print(f"{colorize_entry_number(i)}  {time_str} [{entry.kind}] {content_colored}{context_str}")

    except (ValueError, TypeError):
        traceback.print_exc()
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        traceback.print_exc()
        print(f"Tag command error: {e}", file=sys.stderr)


def handle_untag_command(args) -> None:
    """Handle untag command - remove tag from entry."""
    try:
        entry_identifier = args.entry_num
        tag = args.tag.strip()

        entry = get_entry_from_recent_list(entry_identifier)
        if not entry:
            print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
            return

        repository = RepositoryFactory.get_repository()
        existing_tags = repository.get_tags(entry.id)

        if tag not in existing_tags:
            print(f"Error: Entry {entry_identifier} does not have tag '{tag}'.", file=sys.stderr)
            return

        repository.remove_tag(entry.id, tag)
        # Mark entry dirty so tag removal syncs to server
        repository.update_entry(entry.id, is_dirty=True)
        print(f"Tag '{tag}' removed from entry {entry_identifier}.")

    except (ValueError, TypeError):
        traceback.print_exc()
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        traceback.print_exc()
        print(f"Untag command error: {e}", file=sys.stderr)


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
        # Mark entry dirty so tag addition syncs to server
        repository.update_entry(entry.id, is_dirty=True)
        print(f"Tag '{tag}' added to entry {entry_num}.")

    except (ValueError, TypeError):
        traceback.print_exc()
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        traceback.print_exc()
        print(f"Add tag error: {e}", file=sys.stderr)


def handle_move(args) -> None:
    """Handle moving an entry to a context."""
    try:
        entry_identifier = args.entry_num  # Can be number or name
        context = args.context.strip() if args.context else None

        # Strip leading = if present (support both "tj mv 4 ctx" and "tj mv 4 =ctx")
        if context and context.startswith('='):
            context = context[1:]

        # Treat "0" as "clear context"
        if context == "0":
            context = None

        entry = get_entry_from_recent_list(entry_identifier)
        if not entry:
            print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
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
                print(f"Entry {entry_identifier} moved to context '{context}'.")
            else:
                print(f"Entry {entry_identifier} context cleared.")
        else:
            print("Error: Failed to move entry.", file=sys.stderr)

    except Exception as e:
        traceback.print_exc()
        print(f"Move error: {e}", file=sys.stderr)


def display_entry_details(entry, entry_identifier=None):
    """Display entry in compact list format plus tags and links.

    Args:
        entry: The Entry object to display
        entry_identifier: Optional identifier string (e.g., "1" or "@name") for display
    """
    from tj.commands.common import format_entry_for_display

    # Display entry in list format
    # Convert string identifier to int if it's a number
    entry_num = None
    if entry_identifier:
        try:
            entry_num = int(entry_identifier)
        except (ValueError, TypeError):
            entry_num = None

    print(format_entry_for_display(entry, entry_num))

    # Get tags
    repository = RepositoryFactory.get_repository()
    tags = repository.get_tags(entry.id)

    # Count visual lines in content
    lines = entry.content.split('\n')
    wrap_width = get_line_wrap_width()
    visual_line_count = 0
    for line in lines:
        if len(line) == 0:
            visual_line_count += 1
        else:
            # Estimate visual lines: divide by wrap_width and round up
            visual_line_count += (len(line) + wrap_width - 1) // wrap_width

    # Show metadata on one line: Lines, Truncation, Tags, Links
    has_truncation = entry.data and 'truncate_lines' in entry.data
    has_links = entry.data and 'links' in entry.data and entry.data['links']

    from tj.colors import SOFT_GREY, RESET
    from tj.timezone_manager import format_time_dashboard
    print()  # Empty line before metadata
    info_parts = [f"Lines: {visual_line_count}"]
    if has_truncation:
        info_parts.append(f"Truncation: {entry.data['truncate_lines']}")
    if tags:
        info_parts.append(f"Tags: {', '.join(tags)}")
    if has_links:
        info_parts.append("Links:")
    created_str = format_time_dashboard(entry.timestamp_created)
    info_parts.append(f"Created: {created_str}")
    print(f"{SOFT_GREY}{'  '.join(info_parts)}{RESET}")

    if has_links:
        from tj.colors import colorize_url
        for link in entry.data['links']:
            title = link.get('title', 'Link')
            url = link.get('url', '')
            # Use standard colorize_url with markdown link format
            markdown_link = f"[{title}]({url})"
            print(f"  {colorize_url(markdown_link)}")


def handle_show(args) -> None:
    """Handle showing entry or context details."""
    from tj.commands.common import extract_context_from_args
    try:
        # If no argument, show the most recently modified entry (last in tj l)
        if not args.entry_num:
            repository = RepositoryFactory.get_repository()
            entries = repository.query_entries()
            if not entries:
                print("No entries found.", file=sys.stderr)
                return
            # Sort by timestamp_modified ascending (same as tj l), take last
            entries.sort(key=lambda e: e.timestamp_modified, reverse=False)
            entry = entries[-1]
            display_entry_details(entry, "latest")
            if not entry.parent_id:
                from tj.state import set_last_parent
                set_last_parent(entry.id)
            return

        # Check if showing context details (tj s =context)
        if args.entry_num.startswith('='):
            context_name = args.entry_num[1:]
            if not context_name:
                print("Error: Empty context name.", file=sys.stderr)
                return

            repository = RepositoryFactory.get_repository()
            context = repository.get_context(context_name)

            if not context:
                print(f"Error: Context '{context_name}' not found.", file=sys.stderr)
                return

            # Display context details
            print(f"Context: {context_name}")
            if context.title:
                print(f"Title: {context.title}")
            if context.description:
                print(f"Description: {context.description}")

            # Show entry count
            entries = repository.query_entries(context=context_name)
            active_entries = [e for e in entries if not getattr(e, 'deleted_at', None)]
            print(f"Entries: {len(active_entries)}")

            # Show creation time
            time_str = format_time_dashboard(context.timestamp_created)
            print(f"Created: {time_str}")
            return

        # Extract context marker if present (e.g., tj s =context underway) - do NOT set current context
        entry_identifier, _ = extract_context_from_args(args.entry_num, [], set_context=False)

        entry = get_entry_from_recent_list(entry_identifier)
        if not entry:
            print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
            return

        display_entry_details(entry, entry_identifier)

        # Set as last parent for sub-items (if not a sub-item itself)
        if not entry.parent_id:
            from tj.state import set_last_parent
            set_last_parent(entry.id)

    except (ValueError, TypeError):
        traceback.print_exc()
        print("Error: Invalid entry number.", file=sys.stderr)
    except Exception as e:
        traceback.print_exc()
        print(f"Show error: {e}", file=sys.stderr)


def handle_pin(args) -> None:
    """Handle moving an entry to top (update timestamp)."""
    try:
        entry_identifier = args.entry_num  # Can be number or name

        entry = get_entry_from_recent_list(entry_identifier)
        if not entry:
            print(f"Error: Entry {entry_identifier} not found.", file=sys.stderr)
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
            print(f"Entry {entry_identifier} moved to top.")
        else:
            print("Error: Failed to move entry to top.", file=sys.stderr)

    except Exception as e:
        traceback.print_exc()
        print(f"Pin error: {e}", file=sys.stderr)
