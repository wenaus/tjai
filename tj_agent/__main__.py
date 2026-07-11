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

    if len(sys.argv) < 2:
        print("Usage: python -m tj_agent "
              "[run|start|stop|restart|status|abort|local-action|git-update]")
        sys.exit(1)

    command = sys.argv[1]

    # Local maintenance commands do not need the entry database. Keeping them
    # ahead of init_db also avoids lock contention with the running sync agent.
    if command == "local-action":
        if len(sys.argv) < 3:
            print("Usage: python -m tj_agent local-action <name>")
            sys.exit(1)
        from tj.config import get_config
        from tj_agent.local_actions import run_action
        name = sys.argv[2]
        action_config = (get_config().get("local_actions") or {}).get(name, {})
        result = run_action(name, action_config)
        print(result.message)
        if result.status == "error":
            sys.exit(1)
        return

    if command == "git-update":
        if len(sys.argv) not in (3, 5) or (
            len(sys.argv) == 5 and sys.argv[3] != "--fetch-url"
        ):
            print("Usage: python -m tj_agent git-update <repo> "
                  "[--fetch-url <url>]")
            sys.exit(1)
        from tj_agent.local_actions import update_git_repo
        fetch_url = sys.argv[4] if len(sys.argv) == 5 else None
        result = update_git_repo(sys.argv[2], fetch_url=fetch_url)
        print(result.message)
        if result.status in ("blocked", "error"):
            sys.exit(1)
        return

    init_db()
    from tj_agent import daemon

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
