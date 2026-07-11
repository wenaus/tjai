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


def update_state(**kwargs) -> None:
    """Update state with key-value pairs and save."""
    state = get_state()
    state.update(kwargs)
    save_state(state)

def _get_agent_status_brief() -> str:
    """Get brief agent status for status line. Returns empty string or status suffix."""
    import json
    import time
    from tj.colors import LIGHT_MINT_GREEN, RED, BRIGHT_YELLOW, RESET
    from tj.config import get_location_name

    location_name = get_location_name()

    status_file = APP_DIR / "agent_status.json"
    if not status_file.exists():
        return f" {BRIGHT_YELLOW}{location_name} agent starting.{RESET}"

    try:
        status = json.loads(status_file.read_text())
    except (json.JSONDecodeError, OSError):
        return f" {BRIGHT_YELLOW}{location_name} agent starting.{RESET}"

    # Check for errors first
    if status.get("last_error"):
        return f" {RED}{location_name} agent error: {status['last_error']}{RESET}"

    # Check last sync time
    last_pull = status.get("last_pull")
    if not last_pull:
        return f" {BRIGHT_YELLOW}{location_name} agent not synced.{RESET}"

    ago = int(time.time() - last_pull)
    interval = status.get("sync_interval", "?")

    if ago < 60:
        return f" {LIGHT_MINT_GREEN}{location_name} agent synced {ago}s ago ({interval}s).{RESET}"
    elif ago < 3600:
        return f" {LIGHT_MINT_GREEN}{location_name} agent synced {ago // 60}m ago ({interval}s).{RESET}"
    else:
        # Stale - warn
        return f" {BRIGHT_YELLOW}{location_name} agent synced {ago // 3600}h ago ({interval}s).{RESET}"


def get_tgbot_status() -> dict:
    """Get Telegram bot status.

    Returns dict with:
        running: bool - whether process is running
        pid: int or None - process ID if running
        exchanges_24h: int - number of entries with 'fromtg' tag in last 24h
    """
    import subprocess
    import time
    from pathlib import Path

    result = {
        'running': False,
        'pid': None,
        'exchanges_24h': 0,
    }

    # Check PID file first
    pidfile = Path('/tmp/tg_bot.pid')
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            # Verify process is actually running
            proc = subprocess.run(['ps', '-p', str(pid), '-o', 'cmd='],
                                  capture_output=True, text=True)
            if 'tg_bot' in proc.stdout:
                result['running'] = True
                result['pid'] = pid
        except (ValueError, OSError):
            pass

    # Fallback: search for process
    if not result['running']:
        try:
            proc = subprocess.run(
                ['pgrep', '-f', r'\.venv/bin/python -m tg_bot'],
                capture_output=True, text=True
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pid = int(proc.stdout.strip().split()[0])
                result['running'] = True
                result['pid'] = pid
        except (ValueError, OSError):
            pass

    # Remote fallback: query server heartbeat if not found locally
    if not result['running']:
        try:
            from tj.server import send_command
            resp = send_command("get_sysconfig")
            if resp.get("status") == "ok":
                heartbeat = resp["result"].get("tg_bot_heartbeat")
                if heartbeat:
                    age = int(time.time() - float(heartbeat))
                    result['remote'] = True
                    result['heartbeat_age'] = age
                    result['running'] = age < 300  # healthy if < 5 min
        except Exception:
            result['remote_error'] = True

    # Count recent exchanges (entries with 'fromtg' tag)
    try:
        from tj.repository_factory import RepositoryFactory
        repository = RepositoryFactory.get_repository()
        cutoff = time.time() - (24 * 60 * 60)
        all_entries = repository.query_entries(tag='fromtg')
        result['exchanges_24h'] = sum(
            1 for e in all_entries
            if e.timestamp_created >= cutoff and not getattr(e, 'deleted_at', None)
        )
    except Exception as e:
        print(f"Warning: tgbot exchange count failed: {e}", file=sys.stderr)

    return result


def display_context() -> None:
    """Prints the current context and agent status at the start of every command response."""
    from datetime import datetime
    from tj.colors import colorize_timestamp, colorize_context
    from tj.timezone_manager import format_time_dashboard
    from tj.repository_factory import RepositoryFactory

    # Get current time in local timezone
    now = datetime.now()
    timestamp_str = format_time_dashboard(now.timestamp())

    state = get_state()
    context_name = state.get("current_context")

    # Build context part
    if context_name:
        repository = RepositoryFactory.get_repository()
        context_obj = repository.get_context(context_name)
        context_part = f"Context is {colorize_context(context_name)}."
        if context_obj and context_obj.description:
            context_part += f" {context_obj.description}"
    else:
        context_part = "Context is clear."

    # Build agent status part (concise)
    agent_part = _get_agent_status_brief()

    print(f"{colorize_timestamp(timestamp_str)} {context_part}{agent_part}")

    # Show clock status if any clocks active today
    from tj.commands.clock import get_clock_status_for_display
    clock_lines = get_clock_status_for_display()
    for line in clock_lines:
        print(line)


def set_last_parent(entry_id: str) -> None:
    """Set the last parent ID for sub-item creation."""
    update_state(last_parent_id=entry_id)


def set_last_list(entry_id: str) -> None:
    """Set the last list ID for list item addition."""
    update_state(last_list_id=entry_id)
