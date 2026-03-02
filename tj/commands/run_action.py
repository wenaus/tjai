"""Run action, wake, and restart agent commands for tj CLI."""

import os
import signal
import sys
import time

from tj.state import get_state


def handle_run_action(args):
    """Queue an action for immediate execution via the scheduler.

    Target can be:
    - A number from the last `tj l actions` listing
    - An entry name (string match)

    Instead of running the action directly, modifies scheduled_time so the
    daemon's normal scheduler loop picks it up. Single code path for all runs.
    """
    target = args.target

    # Resolve target to action entry
    if target.isdigit():
        # Look up from last listing state
        state = get_state()
        last_results = state.get("last_query_results", [])
        idx = int(target)
        if idx < 1 or idx > len(last_results):
            print(f"Error: #{idx} out of range. Run 'tj l actions' first.",
                  file=sys.stderr)
            return
        entry_id = last_results[idx - 1]
    else:
        # Look up by name or content match
        from tj.repository_factory import RepositoryFactory
        repository = RepositoryFactory.get_repository()
        all_entries = repository.query_entries(kind='action')
        active = [e for e in all_entries
                  if not getattr(e, 'deleted_at', None)]

        match = None
        for entry in active:
            if entry.name and entry.name == target:
                match = entry
                break
        if not match:
            # Try data.entry_id match
            for entry in active:
                data = getattr(entry, 'data', None) or {}
                if data.get('entry_id') == target:
                    match = entry
                    break
        if not match:
            # Try content substring match
            for entry in active:
                if target.lower() in entry.content.lower():
                    match = entry
                    break
        if not match:
            print(f"Error: No action found matching '{target}'",
                  file=sys.stderr)
            return
        entry_id = match.id

    # Queue via action_agent.py --queue (modifies scheduled_time + wakes agent)
    import subprocess
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parent.parent.parent / 'scripts'
    agent_script = scripts_dir / 'action_agent.py'

    result = subprocess.run(
        [sys.executable, str(agent_script), '--queue', str(entry_id)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Error queuing action (exit code {result.returncode})",
              file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end='')
    else:
        print("Action queued for immediate execution.")
        print("Agent will pick it up within seconds. See /tjai/agent-queue/ for status.")


def handle_wake_agent(args):
    """Send SIGHUP to the action agent daemon to check for due actions now."""
    from tj.server import send_command

    try:
        resp = send_command("get_sysconfig")
    except Exception as e:
        print(f"Error: Could not reach server: {e}", file=sys.stderr)
        return

    if resp.get("status") != "ok":
        print(f"Error: Server returned: {resp}", file=sys.stderr)
        return

    pid_str = resp["result"].get("action_agent_pid")
    if not pid_str or not pid_str.isdigit():
        print("Error: Action agent not running (no PID in sysconfig)",
              file=sys.stderr)
        return

    pid = int(pid_str)
    try:
        os.kill(pid, signal.SIGHUP)
        print(f"Action agent woken (PID {pid})")
    except ProcessLookupError:
        print(f"Error: Action agent process {pid} not found (stale PID)",
              file=sys.stderr)
    except PermissionError:
        print(f"Error: Permission denied sending signal to PID {pid}",
              file=sys.stderr)


def handle_restart_agent(args):
    """Schedule a graceful action agent restart.

    Sets a sysconfig flag that the action agent checks between runs.
    The agent finishes any in-progress action, then exits.
    Supervisord auto-restarts it with the new deployed code.

    Use after deploying code changes to action_agent.py, action_runner.py,
    or any scripts the action agent imports.
    """
    from tj.server import send_command

    try:
        resp = send_command("set_sysconfig",
                            key="action_agent_restart_requested", value="1")
    except Exception as e:
        print(f"Error: Could not reach server: {e}", file=sys.stderr)
        return

    if resp.get("status") != "ok":
        print(f"Error: Server returned: {resp}", file=sys.stderr)
        return

    print("Action agent restart scheduled.")
    print("It will restart after completing any in-progress action.")
