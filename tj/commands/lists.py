"""List command handlers for tj."""

import sys
from datetime import datetime, timezone

from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state


def detect_list_creation(content: str) -> bool:
    """Check if content indicates list creation.

    Heuristic: content ends with "list" or "checklist"
    Examples: "shopping list", "todo checklist", "bucket list"
    """
    lower = content.lower().strip()
    return lower.endswith("list") or lower.endswith("checklist")


def handle_add_list_item(args) -> None:
    """Add item to last referenced list entry.

    List items are stored as strings in the parent entry's
    data["items"] JSON array. They have no individual IDs or timestamps.
    """
    from tj.state import display_context
    display_context()
    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for list item.", file=sys.stderr)
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
        print(f"Error adding list item: {e}", file=sys.stderr)
