# tjai-agent Design

## Purpose

Persistent daemon that syncs local SQLite with the server at etaverse.com/tjai. Runs on every machine that uses the `tj` CLI.

## Architecture

```
tj CLI → Local SQLite (instant read/write)
              ↓
tjai-agent (daemon):
  - Pushes dirty entries to server
  - Pulls updates from server
  - Merges into local SQLite
  - Runs continuously in background
```

## Sync Protocol

### Push (local → server)

1. Query local SQLite for entries where `is_dirty = 1`
2. Also collect associated tags, sub_notes, and any contexts they reference
3. POST to `https://etaverse.com/tjai/api/sync/push`:
   ```json
   {
     "machine_id": "<uuid>",
     "hostname": "<hostname>",
     "entries": [...],
     "contexts": [...],
     "tags": [...],
     "sub_notes": [...]
   }
   ```
4. On success, set `is_dirty = 0` for pushed entries

### Pull (server → local)

1. Track `last_sync_time` in local `sync_metadata` table
2. GET `https://etaverse.com/tjai/api/sync/pull?since=<last_sync_time>&machine_id=<uuid>`
3. Server returns entries modified since that timestamp
4. Upsert received data into local SQLite (skip if local is newer - see Conflict Avoidance)
5. Update `last_sync_time` to server's `server_time` from response

### Sync Cycle

```
while running:
    push_dirty_entries()
    pull_updates()
    sleep(5 seconds)
```

Push before pull ensures local changes reach server before we pull potentially stale data.

## Conflict Avoidance

Per discussion, we aim to avoid conflicts rather than resolve them.

**Strategy: Last-Write-Wins with Local Priority**

On pull, when merging server data into local:
- If local entry has `is_dirty = 1`, skip server version (local pending push takes priority)
- If local `timestamp_modified >= server timestamp_modified`, skip (local is same or newer)
- Otherwise, upsert server version

This works because:
- Single user, fast sync interval (5s) makes true conflicts rare
- Local edits are pushed within seconds
- If somehow both machines edit same entry offline, the last push wins on server, and that propagates to other machines

**Edge case:** Extended offline on two machines editing same entry. Acceptable loss for a personal app - one version wins, no data corruption.

## Machine Identity

Each machine needs a stable unique ID stored in `~/.tjai/machine_id`. Generated once on first agent run (UUID4).

## Daemon Lifecycle

### User-mode systemd

Use systemd user services (`systemctl --user`). This is the modern Linux approach for user daemons:
- Starts automatically on user login
- Restarts on crash
- Proper logging via journald
- No root required

Unit file at `~/.config/systemd/user/tjai-agent.service`:
```ini
[Unit]
Description=tjai sync agent
After=network-online.target

[Service]
Type=simple
ExecStart=/path/to/tjai/agent/tjai-agent
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### Transparent Management

**tj auto-starts agent if needed.** On every `tj` command:
1. Check if agent is running (via systemctl --user is-active)
2. If not running, start it automatically
3. No user intervention required

### Status Display

Every `tj` command shows agent health in the first status line:
```
Fri 11/28 15:45 =tjai [sync: 3s ago]
```
Or if there's a problem:
```
Fri 11/28 15:45 =tjai [sync: OFFLINE 2m]
```

The existing status line that shows time and context gets extended with sync status.

### tj Commands for Agent

```bash
tj admin agent           # Show detailed agent status
tj admin agent start     # Manually start (normally automatic)
tj admin agent stop      # Stop agent
tj admin agent restart   # Restart agent
tj admin agent log       # Show recent agent log entries
```

### Installation (Transparent)

On every `tj` command:
1. Check if agent is running
2. If not, check if daemon service is installed
3. If not installed, install it automatically (detect OS, create service file)
4. Start the daemon
5. Proceed with command

User never needs to think about it.

**Linux (systemd user service):**
- Service file: `~/.config/systemd/user/tjai-agent.service`
- Commands: `systemctl --user enable --now tjai-agent`

**macOS (launchd LaunchAgent):**
- Plist: `~/Library/LaunchAgents/com.tjai.agent.plist`
- Commands: `launchctl load <plist>`

**Manual override:** `tj admin install_daemon` for manual intervention if needed.

### Health Tracking

Agent writes to `~/.tjai/agent_status.json`:
```json
{
  "last_push": 1764344263.5,
  "last_pull": 1764344263.8,
  "last_error": null,
  "entries_pending": 0
}
```

This file is read by `tj` for the status line display. Fast (no IPC needed).

## Configuration

In `~/.tjai/config.json`:
```json
{
  "sync_server": "https://etaverse.com/tjai",
  "sync_interval_seconds": 5,
  "agent_log_level": "INFO"
}
```

## File Structure

```
tjai/
├── tj_agent/
│   ├── __init__.py
│   ├── __main__.py      # Entry point: python -m tj_agent
│   ├── daemon.py        # Daemon lifecycle (start/stop/status)
│   ├── sync.py          # Push/pull logic
│   └── client.py        # HTTP client for server API
```

## Authentication

For now: None. Single-user personal app, server not exposed to untrusted clients.

Future if needed: API key in config.json, sent as header.

## Error Handling

- Network failures: Log warning, retry next cycle
- Server errors (5xx): Log, retry next cycle
- Invalid response: Log error, skip cycle
- Local DB errors: Log error, exit (needs manual intervention)

No exponential backoff for now - 5s interval is already conservative.

## Questions

1. **Daemon manager:** systemd (Linux) / launchd (macOS), transparent install.

2. **Initial sync:** Uses the normal sync cycle (push dirty, then pull).

3. **Deleted entries:** Soft delete (`deleted_at` timestamp) syncs like any other field.
