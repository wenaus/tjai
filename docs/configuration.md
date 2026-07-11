# Installation & Configuration

## Quick Start

### 1. Make executable

```bash
cd /path/to/tjai
chmod +x tj.py
```

### 2. Set up the `tj` command

Add to `~/.bashrc`:

```bash
tj() { python3 /path/to/tjai/tj.py "$@"; }
```

Reload: `source ~/.bashrc`

**Why a function?** Enables natural, unquoted commands: `tj my memory entry` instead of `tj "my memory entry"`

### 3. Verify

```bash
tj h  # Show help
tj    # Show status
```

### 4. Start using

```bash
tj p I prefer dark mode in all applications     # Profile fact
tj =myproject                                   # Switch to project context
tj https://example.com Useful resource          # Bookmark
tj do Review PRs                                # Todo
tj l                                            # List entries
```

## Database

The primary database is **PostgreSQL** on the server (source of truth). Each client machine has a **local SQLite cache** that syncs automatically via the tjai-agent daemon.

Local SQLite location is configurable. Default: `~/Dropbox/Current/tjai_{location}.db` where `{location}` is your machine name. Each machine has its own file; sync happens via REST API, not Dropbox.

```bash
tj config show  # Check current database location
```

### Database MCP (read-only SQL access)

A read-only **Postgres MCP** server (`postgres-mcp`, "Postgres MCP Pro") lets
Claude Code query the PostgreSQL database directly — schema inspection, ad-hoc
`SELECT`, and index/health analysis. It is a per-developer client tool, not a
deployed service, and is distinct from the MCP server tjai itself exposes (see
[mcp.md](mcp.md)).

Install (per developer machine):

```bash
uv tool install postgres-mcp        # -> ~/.local/bin/postgres-mcp
# or: pipx install postgres-mcp
```

Register with Claude Code in restricted (read-only) mode:

```bash
claude mcp add postgres-tjai -- \
  postgres-mcp --access-mode restricted \
  postgresql://<user>@<host>:<port>/<database>
```

On the production host the connection is `postgresql://tjai@localhost:5432/tjai`.
Keep `--access-mode restricted` unless you have a specific need for writes; it
enforces read-only and guards against unsafe or heavy queries.

Verify:

```bash
claude mcp list        # postgres-tjai: ... ✓ Connected
```

A newly added server is not live in an already-open Claude Code session until
restart — `claude mcp list` only probes the config. Restart, then confirm with a
`list_schemas` call or `SELECT version()`.

## Configuration File

`~/.tjai/config.json` — created automatically on first run:

| Key | Default | Description |
|-----|---------|-------------|
| `db_dir` | `~/Dropbox/Current` | Database directory (any local path) |
| `backup_dir` | — | Backup directory location |
| `backup_interval_hours` | 1 | Auto-backup frequency |
| `recent_entries_hours` | 24 | Hours included in "recent" queries |
| `location_name` | — | Machine identifier (e.g., "StudioMax", "ec2dev") |

Note: `sync_interval_seconds` is server-side via `tj admin agent interval`.

## Backup & Restore

```bash
tj backup                    # Manual backup
tj dump > restore.sh         # Database as executable commands
# Later: source restore.sh   # Recreate database
```

The dump format outputs all contexts and entries as `tj` commands with timestamps preserved via `at=` parameters.

## Development Setup

No external dependencies — uses Python standard library only.
The CLI and sync agent support Python 3.11 and newer. On Python versions before
3.14, TJAI supplies its own UUIDv7 implementation.

```bash
chmod +x tj.py
# Add tj function to ~/.bashrc (see above)
./test_all.sh  # Run test suite
```

### Multi-line Input

```bash
echo "Multi-line content" > /tmp/entry.txt
tj -f /tmp/entry.txt
# or
tj e  # Opens $EDITOR
```
