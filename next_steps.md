# Next Steps

## Current State (Nov 28, 2025)

Sync infrastructure is implemented and working on Linux (ec2dev) and WSL2 (Precision5820).

**What's done:**
- Django REST API server at etaverse.com/tjai (PostgreSQL backend)
- tj_agent sync daemon - pushes dirty entries, pulls updates every 5s
- systemd (Linux) and launchd (macOS) daemon management
- Location-based DB naming: `tjai_{location}.db` - prevents Dropbox conflicts
- Location-based backups: `tjai_{location}_{datetime}.db`
- Agent status on every command: "ec2dev agent synced 5s ago."
- Auto-start agent on `tj` command if not recently active
- `tj admin agent [start|stop|restart|install|log|location]` commands

**Current issue: macOS launchd not working**

On Mac, `tj` always shows "agent starting." - the launchd agent isn't running.

Debug steps:
1. `ls ~/Library/LaunchAgents/com.tj*` - check if plist exists
2. `launchctl list | grep tj` - check if loaded
3. `cat ~/.tjai/agent.log` - check for errors
4. `tj admin agent` - CLI agent status

Likely issues:
- Plist not installed (check LAUNCHD_PLIST_FILE path in daemon.py)
- Agent crashing on startup (check log)
- Wrong paths in plist (python path, PYTHONPATH)

Key files:
- `tj_agent/daemon.py` - daemon lifecycle, launchd plist generation
- `tj_agent/sync.py` - push/pull logic
- `tj_agent/client.py` - HTTP client for server API
- `tj/cli.py` - `_ensure_agent_running()` auto-starts agent
- `tj/state.py` - `_get_agent_status_brief()` reads agent status

## Pending

1. **Move backup to agent** - backup only when dirty=0 and recently synced, removes latency from tj commands

2. **MCP server in agent** - expose tj to Claude Code

## Under consideration: Obsidian integration

Complement tj's quick captures with Obsidian's rich markdown documents.
