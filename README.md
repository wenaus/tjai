# tjai - Your Personal AI Memory Aid

## Purpose

`tjai` is a personal AI assistant, companion, and memory aid, operated via a concise and powerful command-line interface (`tj`). Its ultimate purpose is to serve as a definitive, structured **"me descriptor"** — a single source of truth about your life, projects, and knowledge that can be used to provide deep context to other AI systems.

This project is built on an offline-first, distributed architecture. The `tj` client works locally, syncing to a personal cloud backend, ensuring it's always fast, available, and resilient. See [DESIGN.md](DESIGN.md) for architecture details.

## Core Concepts

*   **Concise:** All commands are designed to be as short as possible.
*   **Smart:** The tool intelligently detects input type (URL, date, text) to perform the right action.
*   **Contextual:** A global "context" can be set to automatically group all subsequent entries under a project or topic.
*   **Timestamped:** Every piece of information is automatically timestamped.
*   **Interactive:** Query results are numbered, allowing for easy modification of entries.
*   **Types vs Presentation Formats:** Entry types (kinds) like `journal`, `memory`, `todo` define what data is stored. Presentation formats like `calendar` (`tj c`) and `list` (`tj l`) define how entries are displayed. For example, journal entries can be viewed in calendar format (future-focused, concise, highlighting upcoming meetings) or potentially in diary format (past-focused, showing full long entries). This separation allows the same data to be presented in multiple ways optimized for different use cases.

## Installation & Quick Start

### 1. Make the script executable

```bash
cd /path/to/tjai
chmod +x tj.py
```

### 2. Set up the `tj` command

Add this bash function to `~/.bashrc`:

```bash
tj() { python3 /path/to/tjai/tj.py "$@"; }
```

Replace `/path/to/tjai` with your actual path. Example:

```bash
tj() { python3 ~/github/tjrepo/tjai/tj.py "$@"; }
```

Reload your shell:

```bash
source ~/.bashrc
```

**Why a function?** Enables natural, unquoted commands: `tj my memory entry` instead of `tj "my memory entry"`

### 3. Verify installation

```bash
tj h  # Show help
tj    # Show status (database location configured in ~/.tjai/config.json)
```

### 4. Start using tj

```bash
tj p I prefer dark mode in all applications     # Add profile fact
tj =myproject                                   # Switch to project context
tj https://example.com Useful resource          # Bookmark a URL
tj do Review PRs                                # Add a todo
tj l                                            # List all entries
```

## Command Structure

*   **AGENT:** `tj admin agent [command]`
    *   `tj admin agent`: Show agent status (running, last sync time, interval)
    *   `tj admin agent start|stop|restart`: Control the sync daemon
    *   `tj admin agent install`: Install the daemon service
    *   `tj admin agent log`: Show recent agent log entries
    *   `tj admin agent sync`: Force full sync (reset and pull all)
    *   `tj admin agent location [name]`: Get/set machine location name
    *   `tj admin agent interval [seconds]`: Get/set sync interval (server-wide)
*   **DASHBOARD:** `tj hey`
    *   Shows a personal dashboard of context, todos, and other evolving information.
*   **LIST:** `tj l [c|t|p|b|d|ai]`
    *   `l c`: Lists all contexts with entry counts.
    *   `l t`: Lists all tags with counts.
    *   `l p/b/d/ai`: Lists profiles/bookmarks/todos/AI guidelines.
    *   `l priority`: Lists all prioritized entries, sorted by priority (p=1 first).
    *   `l archive`: Lists archived entries.
    *   `l --all`: No truncation (full content).
    *   `l --clean`: Content only, no preamble.
*   **CONTEXT:** `tj =<context>` with optional flags
    *   `tj =tjai`: Switch to/create context (terse name only)
    *   `tj =tjai -t AI app development`: Create with title
    *   `tj =tjai -t AI app -d Personal project notes`: Create with title + description
    *   `tj =context <text>`: Create entry in specified context (overrides active context)
    *   `tj =0`: Clear active context
    *   `tj =0 <text>`: Create context-free entry
*   **CREATE:**
    *   `tj <text> ...`: Default; creates a new memory.
    *   `tj m <text>`: Creates a memory entry.
    *   `tj do <text>`: Creates a todo item (alias: todo).
    *   `tj p <fact>`: Adds a persistent fact to your profile.
    *   `tj ai <guideline>`: Adds AI behavioral guideline or instruction.
    *   `tj j <date/time> <content>`: Creates journal entry (tomorrow, mon-sun, HH:MM, mmdd, YYYYMMDD)
    *   `tj <YYYYMMDD> ...`: Creates a new journal entry.
    *   `tj <url> ...`: Creates a new bookmark.
    *   *(All creation commands auto-apply current context and can include `:tags`)*.
    *   **Named entries:** `tj @budget Q4 planning` creates named entry
    *   **Priority:** `tj task p=1` sets priority (1=highest)
    *   **Status:** `tj task s=active` sets status (active, done, blocked, etc.)
    *   **Links:** `tj meeting //https://url` or `tj meeting [Title](https://url)`
    *   Inline context: `tj =tjai meeting notes` (switches to tjai, creates entry)
    *   Multi-line input: `tj -f filename.txt` or `tj e` (opens editor)
    *   Timestamp override: `tj at=YYYYMMDD/HH:MM <content>` to set custom creation time
*   **MODIFY:**
    *   `tj . <content>`: Adds a sub-item to last parent entry.
    *   `tj + <item>`: Adds item to current list.
    *   `tj e`: Create new entry in $EDITOR.
    *   `tj e <n>`: Edit entry `<n>` in $EDITOR.
    *   `tj e <n> <text>`: Replace entry `<n>` content (with confirmation).
    *   `tj s <n>` or `tj s @name`: Show entry details.
    *   `tj t <n> <tag>`: Add tag to entry.
    *   `tj mv <n> <context>`: Move entry to context.
    *   `tj ^ <n>`: Pin entry to top (update timestamp).
    *   **Numbered shortcuts:** Quick metadata modifications on entry `<n>`:
        *   `tj <n> @name`: Assign name to entry.
        *   `tj <n> @0`: Clear name from entry.
        *   `tj <n> =context`: Set context on entry.
        *   `tj <n> =0`: Clear context on entry.
        *   `tj <n> :tag`: Add tag to entry.
        *   `tj <n> p=N`: Set priority on entry.
        *   `tj <n> s=status`: Set status on entry.
    *   `tj d <n>` or `tj d @name`: Delete entry (with confirmation).
*   **CALENDAR:**
    *   `tj c`: View calendar (default 30 days)
    *   `tj c t`: Today only
    *   `tj c w`: This week
    *   `tj c m`: This month
    *   `tj c 60`: Next 60 days
    *   `tj c t+1`: Tomorrow
    *   `tj c w-1`: Last week
    *   `tj y`: Year summary with month headers and event counts
*   **CLOCK (time tracking):**
    *   `tj start [time]`: Start time clock (e.g., `tj start 9am`)
    *   `tj stop [time]`: Stop clock and show session summary
    *   `tj break <duration>`: Add break time (e.g., `30` for minutes, `1h` for hours)
    *   Clock status (elapsed, work, breaks) shown in header when active
*   **QUERY:** `tj q ...`
    *   `q [b|r|d|p]`: By type: **b**ookmark, **r**emembered, **d**o, **p**rofile.
    *   `q [t|w|m]`: By time: **t**oday, **w**eek, **m**onth.
    *   `q =<context>`: By context.
    *   `q :<tag>`: By tag.
*   **DUMP:** `tj dump`
    *   Outputs entire database as executable tj commands for backup/restore.
*   **SYNC:** `tj sync`
    *   Forces a manual sync with the remote server.
*   **CONFIG:** `tj config show`
    *   Shows current configuration including database path.
*   **HELP:** `tj h`
    *   Prints a command summary.

## Database and Configuration

### Database Location

The database location is **configurable** and can be stored anywhere you choose. Default is `~/Dropbox/Current/tjai_{location}.db` where `{location}` is your machine name (e.g., `tjai_MacbookPro.db`). Each machine has its own database file; sync happens via the REST API server, not Dropbox. For non-Dropbox setups, set `db_dir` in `~/.tjai/config.json` (e.g., `~/work/tjai`).

**Check current database location:**

```bash
tj config show
```

**Configuration file location:** `~/.tjai/config.json`

The configuration file is created automatically on first run and includes:

*   `db_dir`: Database directory (default: `~/Dropbox/Current`, or any local path like `~/work/tjai`)
*   `backup_dir`: Backup directory location
*   `backup_interval_hours`: How often to auto-backup (default: 1 hour)
*   `recent_entries_hours`: How many hours to include in "recent" queries (default: 24)
*   `location_name`: Machine identifier for location-specific DB naming (e.g., "StudioMax", "ec2dev")

Note: `sync_interval_seconds` is controlled server-side via `tj admin agent interval`.

### Backup and Restore

**Manual backup:**

```bash
tj backup
```

**Dump database as executable commands:**

```bash
tj dump > restore.sh
# Later: source restore.sh to recreate database
```

The dump format outputs all contexts and entries as `tj` commands with original timestamps preserved via `at=` parameters. This provides a human-readable, executable backup format.

## Server Deployment

The tjai Django web server provides the REST API for sync and a web dashboard.

### Production Setup (etaverse.com)

- **Deployed path:** `/var/www/tjai/`
- **Virtual environment:** `/var/www/tjai/.venv`
- **Apache config:** `/etc/apache2/sites-enabled/etaverse.conf`
- **URL mount point:** `https://etaverse.com/tjai/`

### Deploy script

```bash
cd /home/admin/github/tjrepo/tjai
./deploy/update_from_dev.sh
```

This rsyncs code to `/var/www/tjai/`, installs requirements, and runs migrations.

### Manual Django commands

```bash
source /var/www/tjai/.venv/bin/activate
cd /var/www/tjai
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### Endpoints

- `/tjai/` - Landing page
- `/tjai/login/` - Authentication
- `/tjai/dashboard/` - Web dashboard (requires login)
- `/tjai/api/health` - Health check
- `/tjai/api/sync/push` - Push dirty entries from client
- `/tjai/api/sync/pull` - Pull updates to client
- `/tjai/api/command` - Server commands (sysconfig)
- `/tjai/mcp/` - MCP (Model Context Protocol) server for AI assistants

### Claude Code MCP Integration

The tjai MCP server endpoint is `https://etaverse.com/tjai/mcp/` (HTTP transport).

**Project-level config:** A `.mcp.json` file in this directory auto-configures the tjai MCP server when Claude Code is launched from here:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/"
    }
  }
}
```

**Global config:** To add tjai globally via CLI:

```bash
claude mcp add --transport http tjai https://etaverse.com/tjai/mcp/
```

### Claude Code Settings Example

Full `~/.claude/settings.json` with tjai MCP server, permissions, and status line:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/"
    }
  },
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  },
  "permissions": {
    "allow": [
      "Bash(ls:*)",
      "Bash(wc:*)",
      "Bash(grep:*)",
      "mcp__tjai__get_calendar",
      "mcp__tjai__get_profile",
      "mcp__tjai__get_ai_guidance",
      "mcp__tjai__get_todos",
      "mcp__tjai__get_memories",
      "mcp__tjai__list_contexts",
      "mcp__tjai__search_entries",
      "mcp__tjai__create_entry",
      "mcp__tjai__get_entry",
      "mcp__tjai__get_server_instructions",
      "WebSearch",
      "WebFetch"
    ],
    "defaultMode": "default"
  },
  "alwaysThinkingEnabled": true
}
```

**Status line script** (`~/.claude/statusline.sh`):

```bash
#!/bin/bash
input=$(cat)
MODEL=$(echo "$input" | jq -r '.model.display_name')
USED=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
REMAINING=$(echo "$input" | jq -r '.context_window.remaining_percentage // 100')
echo "[$MODEL] ${USED}% used | ${REMAINING}% remaining"
```

### Claude.ai Integration

Full support for Claude.ai across all platforms:

- **Desktop app** (macOS, Windows)
- **Browser** (claude.ai)
- **Mobile** (iOS, Android)
- **Voice** - create memories hands-free while driving

**Setup:** Settings → Connectors → Add custom connector → `https://etaverse.com/tjai/mcp`

Claude.ai authenticates via OAuth 2.1 (Auth0). Once connected, Claude can read your calendar, todos, memories, and create new entries. All AI-created entries are automatically tagged with `fromai` for easy identification.

### OAuth 2.1 Authentication (Technical Details)

The MCP server supports OAuth 2.1 authentication via [Auth0](https://auth0.com) for Claude.ai third-party connector integration.

**Auth0 Configuration:**

| Setting | Value |
|---------|-------|
| Domain | `dev-yjnmn4q2uqphuam2.us.auth0.com` |
| Client ID | `KDoHUD5L0xydOVJywP5f9DoByTpkeOg9` |
| API Identifier | `https://etaverse.com/tjai/mcp` |
| Callback URL | `https://claude.ai/api/mcp/auth_callback` |

**Authentication Modes:**

- **Claude.ai (web)**: OAuth 2.1 with PKCE via Auth0
- **Claude Code (CLI)**: Direct HTTP, no auth required (local config)

**Environment Variables (server):**

```bash
AUTH0_DOMAIN=dev-yjnmn4q2uqphuam2.us.auth0.com
AUTH0_CLIENT_ID=KDoHUD5L0xydOVJywP5f9DoByTpkeOg9
AUTH0_CLIENT_SECRET=<secret>  # Do not commit
AUTH0_API_IDENTIFIER=https://etaverse.com/tjai/mcp
```

## Development Setup

To set up your development environment and run tests:

1.  **Make Executable:**
    Run the following command from the `tjai` directory:

    ```bash
    chmod +x tj.py
    ```

    **Note:** No external dependencies required - uses Python standard library only.

2.  **Set up `tj` Function (Recommended):**
    For convenience, add this function to `~/.bashrc`:

    ```bash
    tj() { python3 ~/github/tjrepo/tjai/tj.py "$@"; }
    ```

    Then `source ~/.bashrc`.

    This allows you to use natural commands without quoting:
    - `tj my memory entry`
    - `tj =work meeting notes`
    - `tj @budget Q4 planning p=1 s=active`

3.  **Run Tests:**
    Run the comprehensive test suite:

    ```bash
    ./test_all.sh
    ```

### Testing Multi-line Input

For multi-line entries, use file input:

```bash
echo "Multi-line content here
More lines
Even more" > /tmp/entry.txt
tj -f /tmp/entry.txt
```

Or use the editor command:

```bash
tj e  # Opens $EDITOR for new entry
```
