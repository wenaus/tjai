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
        return {"current_context": None, "last_entry_id": None}
    with open(STATE_PATH, 'r') as f:
        return json.load(f)

def save_state(state: Dict[str, Any]) -> None:
    """Saves the application's state to the state file."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

def display_context() -> None:
    """Prints the current context if it's set."""
    state = get_state()
    if state.get("current_context"):
        print(f"Context: {state['current_context']}", file=sys.stderr)
