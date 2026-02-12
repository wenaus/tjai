# tjai-agent

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

## Location-Based Database

Each machine has its own database file: `tjai_{location}.db` (e.g., `tjai_MacbookPro.db`, `tjai_ec2dev.db`).

This prevents Dropbox conflicts - sync happens through the server, not filesystem.

On first run, if `location_name` is not set, you'll be prompted. Set it with:
```bash
tj admin agent location MyMachineName
```

## Agent Commands

```bash
tj admin agent              # Show status (running, last sync, interval)
tj admin agent start        # Start daemon
tj admin agent stop         # Stop daemon
tj admin agent restart      # Restart daemon
tj admin agent install      # Install daemon service
tj admin agent log          # Show recent log entries
tj admin agent sync         # Force full sync (reset and pull everything)
tj admin agent location     # Show location name
tj admin agent location X   # Set location name
tj admin agent interval     # Show sync interval
tj admin agent interval 30  # Set sync interval (server-wide, affects all machines)
```

## Status Line

Every `tj` command shows agent status:
```
Fri 11/28/14:20 Context is clear. ec2dev agent synced 5s ago (30s).
```

Format: `{location} agent synced {time} ago ({interval}).`

Colors:
- Green: synced within interval
- Yellow: stale (> 1 hour) or starting
- Red: error

## Sync Protocol

### Push (local → server)

1. Query local SQLite for entries where `is_dirty = 1`
2. POST to `https://etaverse.com/tjai/api/sync/push`
3. On success, set `is_dirty = 0` for pushed entries

### Pull (server → local)

Paginated — server returns entries in batches of 500, using cursor-based pagination by `(timestamp_modified, id)` to handle same-timestamp boundaries.

1. GET `https://etaverse.com/tjai/api/sync/pull?since={last_sync_time}&after_id={cursor}`
2. Server returns batch of entries modified since that timestamp, plus `has_more` flag
3. Upsert received data (skip if local is dirty or newer)
4. If `has_more`, advance cursor to last entry's `(timestamp_modified, id)` and repeat
5. When `has_more` is false, update `last_sync_time` to server's timestamp
6. Apply `sysconfig` (e.g., sync interval)

Normal incremental syncs complete in 1 batch. Full resyncs (`tj admin agent sync`) page through all entries safely regardless of volume.

### Sync Cycle

```
while running:
    push_dirty_entries()
    pull_updates()  # paginated batches, includes sysconfig
    sleep(sync_interval)  # from server sysconfig
```

## Server Configuration (sysconfig)

System-wide configuration is stored server-side in the `sysconfig` table:

| Key | Description |
|-----|-------------|
| `sync_interval_seconds` | How often agents sync (default: 30) |

Set via: `tj admin agent interval 30`

This calls `/api/command` endpoint using stdlib `urllib.request` (no dependencies).

## Conflict Avoidance

**Strategy: Last-Write-Wins with Local Priority**

On pull, when merging server data:
- If local entry has `is_dirty = 1`, skip server version
- If local `timestamp_modified >= server timestamp_modified`, skip
- Otherwise, upsert server version

Single user + fast sync = conflicts are rare.

## Daemon Lifecycle

### Linux (systemd user service)
- Service file: `~/.config/systemd/user/tjai-agent.service`
- Auto-starts on login, restarts on crash

### macOS (launchd)
- Plist: `~/Library/LaunchAgents/com.tjai.agent.plist`

### Auto-start

On every `tj` command, if agent hasn't synced recently (30s), it auto-starts.
No user intervention required.

## Files

```
~/.tjai/
├── config.json           # Local config (location_name, db_dir, etc.)
├── agent_status.json     # Status for tj to read (last_pull, sync_interval)
├── machine_id            # Stable UUID for this machine
└── agent.log             # Log file (macOS)

{db_dir}/                 # Configured in ~/.tjai/config.json (default: ~/Dropbox/Current)
├── tjai_MacbookPro.db    # Location-specific database
├── tjai_ec2dev.db
└── tjai_backups/         # Backups: tjai_{location}_{datetime}.db (or separate backup_dir)

tjai/
├── tj_agent/
│   ├── __init__.py
│   ├── __main__.py       # Entry point: python -m tj_agent run
│   ├── daemon.py         # Daemon lifecycle
│   ├── sync.py           # Push/pull logic
│   └── client.py         # HTTP client (uses urllib, no deps)
└── tj/
    └── server.py         # HTTP client for tj (uses urllib, no deps)
```

## Server API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/api/sync/push` | POST | Push dirty entries |
| `/api/sync/pull` | GET | Pull updates + sysconfig (paginated, 500/batch) |
| `/api/command` | POST | Execute commands (set_sysconfig, get_sysconfig) |

## Server Deployment (ec2dev)

Django runs on ec2dev via Apache mod_wsgi.

**Key paths:**
- Git repo: `/home/admin/github/tjrepo/tjai`
- Deployment: `/var/www/tjai` (not a git repo, Apache serves from here)
- Apache config: `/etc/apache2/sites-enabled/etaverse.conf`
- Apache logs: `/var/log/apache2/etaverse_ssl_error.log`

**Deploy after code changes:**
```bash
sudo cp /home/admin/github/tjrepo/tjai/tjai_app/views.py /var/www/tjai/tjai_app/
sudo systemctl reload apache2
curl -s https://etaverse.com/tjai/api/health  # verify
```

**Full deploy (all files):**
```bash
sudo rsync -av --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    /home/admin/github/tjrepo/tjai/ /var/www/tjai/
sudo systemctl reload apache2
```
