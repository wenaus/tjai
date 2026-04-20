"""Entry point for tj_agent: python -m tj_agent [command]"""

import logging
import sys

from tj.database import init_db


def setup_logging():
    """Configure logging for agent."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main():
    setup_logging()
    init_db()

    from tj_agent import daemon

    if len(sys.argv) < 2:
        print("Usage: python -m tj_agent "
              "[run|start|stop|restart|status|abort]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "run":
        # Run sync loop (called by systemd/launchd)
        daemon.run_forever()

    elif command == "start":
        if daemon.ensure_running():
            print("Agent started")
        else:
            print("Failed to start agent")
            sys.exit(1)

    elif command == "stop":
        if daemon.stop_daemon():
            print("Agent stopped")
        else:
            print("Failed to stop agent")
            sys.exit(1)

    elif command == "restart":
        if daemon.restart_daemon():
            print("Agent restarted")
        else:
            print("Failed to restart agent")
            sys.exit(1)

    elif command == "status":
        if daemon.is_running():
            print("Agent is running")
        else:
            print("Agent is not running")
            if not daemon.is_daemon_installed():
                print("  (daemon service not installed)")

    elif command == "abort":
        # POST status='failed' to the server for the locally-tracked
        # in-flight claim, if any. Works whether tj_agent is running or
        # not — it only reads the local marker and hits the server API.
        # Intended for scripts/kill_worker.sh to run BEFORE launchctl
        # bootout so server state reflects reality immediately rather
        # than waiting up to 2h for auto-reclaim.
        from tj_agent import abort as abort_mod
        reason = " ".join(sys.argv[2:]) or "operator abort via CLI"
        result = abort_mod.abort_current_claim(reason=reason)
        if result.get("aborted"):
            print(f"Aborted claim: entry={result['entry_id']} "
                  f"capability={result.get('capability')}")
        elif result.get("detail") == "no claim":
            print("No in-flight claim to abort.")
        else:
            print(f"Abort failed: {result.get('detail')}")
            sys.exit(1)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
