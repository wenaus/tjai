import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# --- Constants and Configuration ---
APP_DIR = Path(os.environ.get("TJAI_APP_DIR", Path.home() / ".tjai"))
STATE_PATH = APP_DIR / "state.json"
LAST_QUERY_PATH = APP_DIR / "last_query.json"

# --- State Management ---
def get_state() -> Dict[str, Any]:
    """Reads the application's state from the state file."""
    if not STATE_PATH.exists():
        return {
            "current_context": None,
            "last_entry_id": None,
            "last_parent_id": None,
            "last_list_id": None
        }
    with open(STATE_PATH, 'r') as f:
        state = json.load(f)
        # Ensure new fields exist for backward compatibility
        if "last_parent_id" not in state:
            state["last_parent_id"] = None
        if "last_list_id" not in state:
            state["last_list_id"] = None
        return state

def save_state(state: Dict[str, Any]) -> None:
    """Saves the application's state to the state file."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

def display_context() -> None:
    """Prints the current context at the start of every command response."""
    from datetime import datetime
    from tj.colors import colorize_timestamp, colorize_context
    from tj.timezone_manager import format_time_dashboard
    from tj.repository_factory import RepositoryFactory

    # Get current time in local timezone
    now = datetime.now()
    timestamp_str = format_time_dashboard(now.timestamp())

    state = get_state()
    context_name = state.get("current_context")
    if context_name:
        # Get context object to retrieve description
        repository = RepositoryFactory.get_repository()
        context_obj = repository.get_context(context_name)

        context_display = f"{colorize_timestamp(timestamp_str)} Context is {colorize_context(context_name)}."
        if context_obj and context_obj.description:
            context_display += f" {context_obj.description}"
        print(context_display)
    else:
        print(f"{colorize_timestamp(timestamp_str)} Context is clear")


def set_last_parent(entry_id: str) -> None:
    """Set the last parent ID for sub-item creation.

    Called after creating top-level entries and when showing entry details.
    """
    state = get_state()
    state["last_parent_id"] = entry_id
    save_state(state)


def set_last_list(entry_id: str) -> None:
    """Set the last list ID for list item addition.

    Called after creating list entries.
    """
    state = get_state()
    state["last_list_id"] = entry_id
    save_state(state)
