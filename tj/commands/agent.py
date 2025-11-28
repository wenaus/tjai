"""Agent commands for tj sync daemon management."""

import json
import sys
import time


def handle_agent(args) -> None:
    """Handle agent subcommands."""
    from tj_agent import daemon
    from tj_agent.sync import STATUS_FILE
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
                if status.get("entries_pending", 0) > 0:
                    print(f"Entries pending: {status['entries_pending']}")
            except (json.JSONDecodeError, OSError):
                pass

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

    else:
        print(f"Unknown agent command: {agent_cmd}", file=sys.stderr)
        print("Usage: tj admin agent [start|stop|restart|install|log]")
