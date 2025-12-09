"""Handlers for numbered entry commands (e.g., '5 @name', '3 p=1')."""

import re
import sys
from datetime import datetime, timezone
from typing import Optional

from tj.commands.common import get_entry_from_recent_list, ENTRY_TYPE_MAP
from tj.repository_factory import RepositoryFactory


def _get_entry_or_error(num_identifier: int):
    """Get entry from recent list, print error if not found."""
    entry = get_entry_from_recent_list(num_identifier)
    if not entry:
        print(f"Error: Entry {num_identifier} not found in recent list.", file=sys.stderr)
    return entry


def _update_entry(entry_id: str, num_identifier: int, success_msg: str, **changes) -> bool:
    """Update entry with changes, print result message."""
    repository = RepositoryFactory.get_repository()
    changes['timestamp_modified'] = datetime.now(timezone.utc).timestamp()
    changes['is_dirty'] = True
    success = repository.update_entry(entry_id, **changes)
    if success:
        print(success_msg)
    else:
        print("Error: Failed to update entry.", file=sys.stderr)
    return success


def handle_set_name(num_identifier: int, action_command: str) -> bool:
    """Handle @name - assign name to entry (@0 clears name)."""
    name = action_command[1:]

    # @0 clears the name
    if name == '0':
        entry = _get_entry_or_error(num_identifier)
        if not entry:
            return False
        return _update_entry(entry.id, num_identifier, f"Entry {num_identifier} name cleared", name=None)

    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', name):
        print(f"Error: Invalid name '{name}'. Must start with letter, then alphanumeric/underscore/dash.", file=sys.stderr)
        return False

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    repository = RepositoryFactory.get_repository()
    existing_entry = repository.get_entry_by_name(name, entry.context)
    if existing_entry and existing_entry.id != entry.id:
        context_msg = f" in context '={entry.context}'" if entry.context else " (no context)"
        print(f"Error: Name '@{name}' is already assigned to another entry{context_msg}.", file=sys.stderr)
        print(f"Use 'tj s @{name}' to see the existing entry.", file=sys.stderr)
        return False

    return _update_entry(entry.id, num_identifier, f"Entry {num_identifier} named '@{name}'", name=name)


def handle_set_priority(num_identifier: int, action_command: str) -> bool:
    """Handle p=N - set priority (p=0 removes priority)."""
    try:
        priority = int(action_command[2:])
    except ValueError:
        print(f"Error: Invalid priority '{action_command}'. Use p=N where N is a number.", file=sys.stderr)
        return False

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    priority_value = None if priority == 0 else priority
    msg = f"Entry {num_identifier} priority removed" if priority == 0 else f"Entry {num_identifier} priority set to {priority}"
    return _update_entry(entry.id, num_identifier, msg, priority=priority_value)


def handle_set_status(num_identifier: int, action_command: str) -> bool:
    """Handle s=value - set status."""
    status = action_command[2:]
    if not re.match(r'^\w+$', status):
        print(f"Error: Invalid status '{status}'. Must be alphanumeric.", file=sys.stderr)
        return False

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    return _update_entry(entry.id, num_identifier, f"Entry {num_identifier} status set to '{status}'", status=status)


def handle_set_kind(num_identifier: int, action_command: str) -> bool:
    """Handle k=kind - change entry kind."""
    kind_abbrev = action_command[2:]
    if kind_abbrev not in ENTRY_TYPE_MAP:
        valid_kinds = ', '.join(ENTRY_TYPE_MAP.keys())
        print(f"Error: Invalid kind '{kind_abbrev}'. Valid kinds: {valid_kinds}", file=sys.stderr)
        return False

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    new_kind = ENTRY_TYPE_MAP[kind_abbrev]
    return _update_entry(entry.id, num_identifier, f"Entry {num_identifier} kind changed to {kind_abbrev}", kind=new_kind)


def handle_set_truncation(num_identifier: int, action_command: str) -> bool:
    """Handle l=N - set per-entry truncation (l=0 removes override)."""
    lines_str = action_command[2:]
    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    entry_data = entry.data.copy() if entry.data else {}
    if lines_str == '0':
        entry_data.pop('truncate_lines', None)
        msg = f"Entry {num_identifier} truncation removed (using global)"
    else:
        try:
            entry_data['truncate_lines'] = int(lines_str)
            msg = f"Entry {num_identifier} truncation set to {lines_str} lines"
        except ValueError:
            print("Error: Invalid lines value. Use l=N or l=0 to remove", file=sys.stderr)
            return False

    return _update_entry(entry.id, num_identifier, msg, data=entry_data)


def handle_add_tag(num_identifier: int, action_command: str) -> bool:
    """Handle :tag - add tag to entry."""
    tag = action_command[1:]
    if not tag:
        print("Error: Tag name cannot be empty.", file=sys.stderr)
        return False

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    repository = RepositoryFactory.get_repository()
    try:
        repository.add_tag(entry.id, tag)
        # Mark entry dirty so tag addition syncs to server
        repository.update_entry(entry.id, is_dirty=True)
        print(f"Tag ':{tag}' added to entry {num_identifier}")
        return True
    except Exception as e:
        print(f"Error: Failed to add tag ':{tag}': {e}", file=sys.stderr)
        return False


def handle_set_context(num_identifier: int, action_command: str) -> bool:
    """Handle =context - move entry to context (=0 clears context)."""
    context = action_command[1:]
    if context == '0':
        context = None

    entry = _get_entry_or_error(num_identifier)
    if not entry:
        return False

    msg = f"Entry {num_identifier} moved to context '={context}'" if context else f"Entry {num_identifier} context cleared"
    return _update_entry(entry.id, num_identifier, msg, context=context)


# Dispatch table: prefix -> (handler_function, min_length)
# min_length ensures we match 'p=' not just 'p'
NUMBERED_HANDLERS = [
    ('@', handle_set_name),
    ('p=', handle_set_priority),
    ('s=', handle_set_status),
    ('k=', handle_set_kind),
    ('l=', handle_set_truncation),
    (':', handle_add_tag),
    ('=', handle_set_context),
]


def dispatch_numbered_command(num_identifier: int, action_command: str) -> Optional[bool]:
    """Dispatch a numbered command to the appropriate handler.

    Returns:
        True/False if handled (success/failure), None if not a metadata command.
    """
    for prefix, handler in NUMBERED_HANDLERS:
        if action_command.startswith(prefix):
            return handler(num_identifier, action_command)
    return None
