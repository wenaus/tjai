"""List command handlers for tj."""

import sys
import traceback
from datetime import datetime, timezone

from tj.commands.common import get_entry_from_recent_list
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state


def detect_list_creation(content: str) -> bool:
    """Check if content indicates list creation.

    Heuristic: content ends with "list" or "checklist"
    Examples: "shopping list", "todo checklist", "bucket list"
    """
    lower = content.lower().strip()
    return lower.endswith("list") or lower.endswith("checklist")


def _add_to_named_entry(args) -> None:
    """Append text to a named entry's content.

    Usage: tj + @name item text
    Resolves @name, appends item text as a new line.
    """
    name_arg = args.input[0]  # e.g. "@shopping"
    item_parts = args.input[1:]

    if not item_parts:
        print(f"Error: No text to add. Usage: tj + {name_arg} <text>", file=sys.stderr)
        return

    try:
        entry = get_entry_from_recent_list(name_arg)
        if not entry:
            print(f"Error: Named entry '{name_arg}' not found.", file=sys.stderr)
            return

        item_text = " ".join(item_parts)
        new_content = entry.content + "\n" + item_text

        repository = RepositoryFactory.get_repository()
        now = datetime.now(timezone.utc).timestamp()
        success = repository.update_entry(
            entry.id,
            content=new_content,
            timestamp_modified=now,
            is_dirty=True
        )

        if success:
            print(f"Added to @{entry.name}: {item_text}")
        else:
            print("Error: Failed to update entry.", file=sys.stderr)

    except Exception as e:
        traceback.print_exc()
        print(f"Error adding to named entry: {e}", file=sys.stderr)


def handle_add_list_item(args) -> None:
    """Add item to last referenced list entry.

    List items are stored as strings in the parent entry's
    data["items"] JSON array. They have no individual IDs or timestamps.
    """
    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for list item.", file=sys.stderr)
        return

    # Handle @name syntax: tj + @shopping milk
    if args.input[0].startswith('@'):
        _add_to_named_entry(args)
        return

    try:
        # Get list from state
        state = get_state()
        list_id = state.get("last_list_id")

        if not list_id:
            print("Error: No active list. Create a list entry first.", file=sys.stderr)
            print("Hint: Try 'tj shopping list' then 'tj + milk'", file=sys.stderr)
            return

        # Load list entry
        repository = RepositoryFactory.get_repository()
        list_entry = repository.get_entry(list_id)

        if not list_entry:
            print("Error: List entry not found.", file=sys.stderr)
            # Clear invalid list from state
            state["last_list_id"] = None
            save_state(state)
            return

        if list_entry.kind != "list":
            print(f"Error: Entry is not a list (type: {list_entry.kind}).", file=sys.stderr)
            return

        # Add item to data["items"]
        item_text = " ".join(args.input)
        data = list_entry.data or {}
        items = data.get("items", [])
        items.append(item_text)
        data["items"] = items

        # Update entry
        now = datetime.now(timezone.utc).timestamp()
        success = repository.update_entry(
            list_id,
            data=data,
            timestamp_modified=now,
            is_dirty=True
        )

        if success:
            print(f"Added to '{list_entry.content}': {item_text}")
            print(f"Total items: {len(items)}")
        else:
            print("Error: Failed to add list item.", file=sys.stderr)

    except Exception as e:
        traceback.print_exc()
        print(f"Error adding list item: {e}", file=sys.stderr)
