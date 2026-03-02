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

Location is configurable. Default: `~/Dropbox/Current/tjai_{location}.db` where `{location}` is your machine name. Each machine has its own file; sync happens via REST API, not Dropbox.

```bash
tj config show  # Check current database location
```

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
