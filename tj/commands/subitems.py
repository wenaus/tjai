"""Sub-item command handlers for tj."""

import sys
import uuid
from datetime import datetime, timezone

from tj.config import get_preview_length
from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state


def handle_add_subitem(args) -> None:
    """Add sub-item to last referenced parent entry.

    Sub-items are full Entry objects with their own IDs and timestamps,
    but linked to a parent via parent_id field. Max 1 level deep.
    """
    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for sub-item.", file=sys.stderr)
        return

    try:
        # Get parent from state
        state = get_state()
        parent_id = state.get("last_parent_id")

        if not parent_id:
            print("Error: No parent entry. Create or show an entry first.", file=sys.stderr)
            return

        # Validate parent exists and is not itself a sub-item
        repository = RepositoryFactory.get_repository()
        parent = repository.get_entry(parent_id)

        if not parent:
            print("Error: Parent entry not found.", file=sys.stderr)
            # Clear invalid parent from state
            state["last_parent_id"] = None
            save_state(state)
            return

        if parent.parent_id:
            print("Error: Cannot create sub-item of sub-item (max 1 level deep).", file=sys.stderr)
            return

        # Extract tags from content
        from tj.commands.common import is_valid_tag
        content = " ".join(args.input)
        tags = set()
        for part in args.input:
            if part.startswith(':'):
                tag_name = part[1:]
                if is_valid_tag(tag_name):
                    tags.add(tag_name)

        # Create sub-item entry
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).timestamp()

        entry = Entry(
            id=entry_id,
            parent_id=parent_id,
            content=content,
            kind="memory",
            timestamp_created=now,
            timestamp_modified=now,
            context=parent.context,  # Inherit parent's context
            is_dirty=True
        )

        repository.create_entry(entry)

        # Add tags
        for tag in tags:
            repository.add_tag(entry_id, tag)

        # Update state
        state["last_entry_id"] = entry_id
        # last_parent_id stays the same
        save_state(state)

        preview_len = get_preview_length()
        print(f"Sub-item added: {content[:preview_len]}{'...' if len(content) > preview_len else ''}")

    except Exception as e:
        print(f"Error creating sub-item: {e}", file=sys.stderr)
