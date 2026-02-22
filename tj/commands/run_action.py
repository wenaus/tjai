"""Run action and wake agent commands for tj CLI."""

import os
import signal
import subprocess
import sys

from tj.state import get_state


def handle_run_action(args):
    """Execute an action entry now.

    Target can be:
    - A number from the last `tj l actions` listing
    - An entry name (string match)
    """
    target = args.target

    # Resolve target to entry ID
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

    # Execute via action_agent.py --run
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parent.parent.parent / 'scripts'
    agent_script = scripts_dir / 'action_agent.py'

    print(f"Executing action {entry_id}...")
    result = subprocess.run(
        [sys.executable, str(agent_script), '--run', str(entry_id)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Action failed (exit code {result.returncode})",
              file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end='')
    else:
        print("Done. See /tjai/agent-log/ for details.")


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
