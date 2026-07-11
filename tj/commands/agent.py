"""Agent commands for tj sync daemon management."""

import json
import sys
import time
import traceback
from pathlib import Path

from tj.database import APP_DIR

STATUS_FILE = APP_DIR / "agent_status.json"


def handle_agent(args) -> None:
    """Handle agent subcommands."""
    from tj_agent import daemon
    from tj.config import get_config, save_config

    # Get agent subcommand if any
    agent_cmd = None
    if hasattr(args, 'args') and args.args:
        agent_cmd = args.args[0]

    if agent_cmd == 'location':
        # tj admin agent location [name]
        config = get_config()
        if len(args.args) > 1:
            new_name = args.args[1]
            config['location_name'] = new_name
            save_config(config)
            print(f"Location set to: {new_name}")
        else:
            current = config.get('location_name')
            if current:
                print(f"Location: {current}")
            else:
                import socket
                print(f"Location: (using hostname: {socket.gethostname()})")
        return

    if agent_cmd is None:
        # Show agent status
        running = daemon.is_running()
        installed = daemon.is_daemon_installed()

        print(f"Agent: {'running' if running else 'stopped'}")
        print(f"Agent installed: {'yes' if installed else 'no'}")

        # Show last sync info
        if STATUS_FILE.exists():
            try:
                status = json.loads(STATUS_FILE.read_text())
                if status.get("last_pull"):
                    ago = time.time() - status["last_pull"]
                    print(f"Last sync: {int(ago)}s ago")
                if status.get("last_error"):
                    print(f"Last error: {status['last_error']}")
                    if status.get("last_error_at"):
                        error_age = time.time() - status["last_error_at"]
                        print(f"Error age: {int(error_age)}s")
                    if status.get("last_traceback"):
                        print("Last traceback:")
                        print(status["last_traceback"].rstrip())
                if status.get("entries_pending", 0) > 0:
                    print(f"Entries pending: {status['entries_pending']}")
            except (json.JSONDecodeError, OSError) as exc:
                print(
                    f"Agent status error: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

    elif agent_cmd == 'start':
        if daemon.is_running():
            print("Agent already running")
        elif daemon.ensure_running():
            print("Agent started")
        else:
            print("Failed to start agent", file=sys.stderr)

    elif agent_cmd == 'stop':
        if not daemon.is_running():
            print("Agent not running")
        elif daemon.stop_daemon():
            print("Agent stopped")
        else:
            print("Failed to stop agent", file=sys.stderr)

    elif agent_cmd == 'restart':
        if daemon.restart_daemon():
            print("Agent restarted")
        else:
            print("Failed to restart agent", file=sys.stderr)

    elif agent_cmd == 'install':
        if daemon.is_daemon_installed():
            print("Agent already installed")
        elif daemon.install_daemon():
            print("Agent installed")
        else:
            print("Failed to install agent", file=sys.stderr)

    elif agent_cmd == 'log':
        # Show recent log entries
        from tj.database import APP_DIR
        log_file = APP_DIR / "agent.log"
        if log_file.exists():
            lines = log_file.read_text().splitlines()[-20:]
            for line in lines:
                print(line)
        else:
            print("No agent log found")

    elif agent_cmd == 'sync':
        # Manual full sync - reset sync time and restart daemon
        from tj.database import get_db_connection
        import time as time_module
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated)
            VALUES ('last_sync_time', '0', ?)
            """,
            (time_module.time(),)
        )
        conn.commit()
        print("Sync time reset to 0 for full sync.")

        if not daemon.is_running():
            print("Starting agent...")
            daemon.ensure_running()
        else:
            print("Restarting agent...")
            daemon.restart_daemon()
        print("Full sync triggered.")

    elif agent_cmd == 'interval':
        # tj admin agent interval [seconds]
        from tj.server import send_command

        if len(args.args) > 1:
            new_interval = args.args[1]
            try:
                int(new_interval)  # Validate it's a number
            except ValueError:
                print(f"Invalid interval: {new_interval}", file=sys.stderr)
                return

            try:
                result = send_command("set_sysconfig", key="sync_interval_seconds", value=new_interval)
                if result.get("status") == "ok":
                    print(f"Sync interval set to {new_interval}s")
                else:
                    print(f"Failed: {result.get('error', 'Unknown error')}", file=sys.stderr)
            except Exception as e:
                traceback.print_exc()
                print(f"Failed to set interval: {e}", file=sys.stderr)
        else:
            # Show current interval from status file
            if STATUS_FILE.exists():
                try:
                    status = json.loads(STATUS_FILE.read_text())
                    interval = status.get("sync_interval", "unknown")
                    print(f"Sync interval: {interval}s")
                except (json.JSONDecodeError, OSError):
                    print("Sync interval: unknown")
            else:
                print("Sync interval: unknown (agent not running)")

    else:
        print(f"Unknown agent command: {agent_cmd}", file=sys.stderr)
        print("Usage: tj admin agent [start|stop|restart|install|log|sync|location|interval]")
