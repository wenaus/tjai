# tjai - Your Personal AI Memory Aid

## Purpose

`tjai` is a personal AI assistant, companion, and memory aid, operated via a concise and powerful command-line interface (`tj`). Its ultimate purpose is to serve as a definitive, structured **"me descriptor"** — a single source of truth about your life, projects, and knowledge that can be used to provide deep context to other AI systems.

This project is built on an offline-first, distributed architecture. The `tj` client works locally, syncing to a personal cloud backend, ensuring it's always fast, available, and resilient. See [DESIGN.md](DESIGN.md) for architecture details and [BULK_IMPORT.md](BULK_IMPORT.md) for importing bookmarks from external sources.

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
    *   `tj j <date/time> <content>`: Creates journal entry (yesterday, tomorrow, mon-sun, HH:MM, mmdd, YYYYMMDD)
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
    *   `tj + @name <text>`: Appends text as new line to named entry's content.
    *   `tj e`: Create new entry in $EDITOR.
    *   `tj e [ai|do|p|b|j]`: Create typed entry in $EDITOR.
    *   `tj e <n>`: Edit entry `<n>` in $EDITOR.
    *   `tj e <n> -k`: Edit entry, keep original modification time.
    *   `tj e <n> =ctx`: Set context on entry (no editor).
    *   `tj e <n> <text>`: Replace entry `<n>` content (with confirmation).
    *   `tj s <n>` or `tj s @name`: Show entry details.
    *   `tj s =ctx`: Show context description.
    *   `tj t <n> <tag>`: Add tag to entry.
    *   `tj t- <n> <tag>`: Remove tag from entry.
    *   `tj mv <n> <context>`: Move entry to context.
    *   `tj ^ <n>`: Pin entry to top (update timestamp).
    *   `tj cp <n> <datetime>`: Copy journal entry to new date/time.
    *   `tj archive <n>`: Archive entry.
    *   `tj unarchive <n>`: Unarchive entry.
    *   **Numbered shortcuts:** Quick metadata modifications on entry `<n>`:
        *   `tj <n> @name`: Assign name to entry.
        *   `tj <n> @0`: Clear name from entry.
        *   `tj <n> =context`: Set context on entry.
        *   `tj <n> =0`: Clear context on entry.
        *   `tj <n> :tag`: Add tag to entry.
        *   `tj <n> p=N`: Set priority on entry.
        *   `tj <n> p=0`: Remove priority.
        *   `tj <n> s=status`: Set status on entry.
        *   `tj <n> k=type`: Change kind (ai, b, do, j, m, p).
        *   `tj <n> l=N`: Set truncation lines for display.
        *   `tj <n> l=0`: Remove truncation.
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
    *   `tj stop [time|datetime]`: Stop clock (supports retroactive: `yesterday 5pm`, `20260203/09:30`)
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

### Manual Django commands (production)

```bash
source /var/www/tjai/.venv/bin/activate
cd /var/www/tjai
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### Django commands from dev tree

The dev tree at `/home/admin/github/tjrepo/tjai/` has no `.env` file. Django settings require `DJANGO_DATABASE_URL` and other vars that live in `/var/www/tjai/.env`. That file uses plain `KEY=VALUE` (no `export`), so `set -a` is needed:

```bash
set -a && source /var/www/tjai/.env && set +a
cd /home/admin/github/tjrepo/tjai
.venv/bin/python manage.py makemigrations tjai_app --name <name>
.venv/bin/python manage.py migrate
```

### Endpoints

- `/tjai/` - Dashboard with entry list, filtering by kind/context/tags/status
- `/tjai/login/` - Authentication
- `/tjai/entry/` - Entry detail view with human-readable data display
- `/tjai/daily/` - Daily synopsis (Today in History)
- `/tjai/picks/` - AI-curated news picks triage page
- `/tjai/rss/` - RSS reader with source-grouped triage
- `/tjai/readme/` - Reading list (items tagged :readme)
- `/tjai/system/` - System health monitoring dashboard
- `/tjai/agent-log/` - Action agent execution log
- `/tjai/api/health` - Health check
- `/tjai/api/sync/push` - Push dirty entries from client
- `/tjai/api/sync/pull` - Pull updates to client (paginated, 500 entries/batch)
- `/tjai/api/bulk-import` - Bulk import bookmarks (Bearer token auth)
- `/tjai/api/add-bookmark` - Single bookmark from Chrome extension (Bearer token auth)
- `/tjai/api/add-journal` - Journal entry from Gmail add-on (Bearer token auth)
- `/tjai/api/dialog` - Claude Code dialog turns GET/POST (Bearer token auth)
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

**Claude Code configuration files:**

Settings and status line are maintained in `tjrepo/computers/common/`. Symlink them:

```bash
ln -s ~/github/tjrepo/computers/common/claude-settings.json ~/.claude/settings.json
ln -s ~/github/tjrepo/computers/common/claude-statusline.sh ~/.claude/statusline.sh
```

The settings file configures:
- MCP server connection to tjai
- Pre-approved permissions for read-only MCP tools and common bash commands
- Status line display

The status line shows model, cost, context usage, session duration, and working directory.

### Claude Code Cross-Session Dialog Memory

Every Claude Code conversation is recorded into tjai so that new sessions on any machine can load recent dialog context. This gives Claude continuity across sessions without manual copy-paste.

**How it works:**

```
[Session starts] → SessionStart hook → load.py
  → HTTP GET /api/dialog → fetches recent dialog turns
  → Prints SYSPROMPT.md + formatted dialog to stdout → injected into Claude context

[User submits prompt] → UserPromptSubmit hook → record.py (async)
  → HTTP POST /api/dialog → creates tjai entry with role='user'

[Claude finishes] → Stop hook → record.py (async)
  → Extracts last assistant text from JSONL transcript
  → HTTP POST /api/dialog → creates tjai entry with role='assistant'
```

Dialog entries are stored as: `kind='memory'`, `context='claude-code'`, `tag='ccdialog'`, `is_dirty=0` (server-only, not synced to local clients). Uses `Entry.objects.create()` directly, bypassing the 60s dedup in services.py.

**Hook scripts** are in `computers/common/claude-hooks/`:
- `load.py` — SessionStart hook (synchronous). Fetches dialog, prints SYSPROMPT.md + history to stdout.
- `record.py` — UserPromptSubmit + Stop hook (async). Records prompts and responses.
- `SYSPROMPT.md` — Static context injected at session start.

**Server endpoint:** `api/dialog` (GET + POST, Bearer token auth — same key as Chrome extension/Gmail add-on)

**Configuration:**

Hook paths in `claude-settings.json` reference `~/.claude/hooks/`, which must be symlinked:

```bash
ln -s ~/github/tjrepo/computers/common/claude-hooks ~/.claude/hooks
```

Environment variables required by the hooks:

```bash
export TJAI_API_KEY="$TJAI_GMAIL_ADDON_API_KEY"   # Bearer token
export TJAI_DIALOG_TURNS=10                         # turns to load (0=disabled)
# export TJAI_API_URL=https://etaverse.com/tjai    # default, override if needed
```

The full laptop environment — including `TJAI_GMAIL_ADDON_API_KEY` and all other personal keys — is committed to `computers/laptop/config-files/.env`. On a new Mac, symlink it:

```bash
ln -s ~/github/tjrepo/computers/laptop/config-files/.env ~/.env
```

Ensure `~/.bash_profile` sources `~/.env` (e.g. `source ~/.env`). This provides all hook vars automatically.

**Activation states:** If `TJAI_DIALOG_TURNS` is unset, load.py prints a notice inviting the user to activate it. If set to `0`, it tells you it's disabled. In both cases, no API calls are made and record.py does nothing. If Claude Code won't start due to hook issues, `export TJAI_DIALOG_TURNS=0` bypasses all network activity.

**Error handling:** All errors print to stderr (visible in `claude --verbose`). Hooks always exit 0 so they never block the session. All HTTP calls have a 5-second timeout. Assistant responses are truncated at 4000 chars on record, 2000 chars on display.

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

### Telegram Bot

A personal AI assistant via Telegram with full tjai access, voice dialogue, and location awareness. Designed for hands-free use while driving.

**Features:**
- **Voice I/O** - speak to the bot and hear responses back. Uses OpenAI Whisper for speech-to-text and OpenAI TTS (Nova voice, OGG Opus format) for text-to-speech
- **Voice/text mode toggle** - switch between voice and text responses; mode persists across sessions. Claude adapts response style to the current mode
- **GPS location** - share your Telegram live location and the bot uses reverse geocoding (OpenStreetMap Nominatim) to provide location-aware responses (weather, local info, etc.)
- **Web search and fetch** - Claude server tools (`web_search`, `web_fetch`) with user location context for real-time information
- **Text chat** with Claude Sonnet, with access to all tjai tools (calendar, todos, memories, bookmarks, search)
- **Calendar reminders** - background job checks every 5 minutes for upcoming calendar events and sends a Telegram push notification 15 minutes before
- **Persistent conversation history** - survives bot restarts (stored as tjai entries with tag `tgchat`)
- Single-user authentication via Telegram user ID

**Setup:**

1. Create a bot via [@BotFather](https://t.me/botfather) on Telegram
2. Get your user ID from [@userinfobot](https://t.me/userinfobot)
3. Add to `/var/www/tjai/.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=<from_botfather>
   TELEGRAM_USER_ID=<your_user_id>
   ANTHROPIC_API_KEY=<key>
   OPENAI_API_KEY=<key>  # Whisper STT and TTS
   ```
4. Install dependencies: `pip install -r requirements-tgbot.txt`
5. Start: `./deploy/restart_tgbot.sh --sync`

**Usage:**
- `/start` - Initialize bot
- `/clear`, `/voice`, `/text` - Typed commands
- **Text message** - Chat with AI assistant
- **Voice message** - Speak to the bot; it transcribes, processes, and responds with voice
- **Share location** - Send your GPS location to enable location-aware responses

**Voice commands** (say or type) - instant response, no LLM:
- "use voice" - switch to voice responses
- "use text" - switch to text responses
- "clear history" - start fresh conversation
- "repeat that" - repeat last response
- "save that" - save last response to tjai memory
- "get shopping" - retrieve named entry (case-insensitive)
- "add shopping pizza dough" - append to named entry
- "memo buy milk" - save text as memory
- "calendar" - today and tomorrow's events
- "journal tomorrow 2pm doctors" - create calendar event
- "recent" - last 5 entries
- "voice help" - list voice commands

**Management:**
```bash
./deploy/restart_tgbot.sh          # Restart bot
./deploy/restart_tgbot.sh --sync   # Sync code from dev and restart
tj                                  # Shows bot status in CLI
```

Entries created via Telegram are tagged with `fromtg` and `fromai`.

### AI Picks (Curated News)

An AI-driven news curation system that researches tech/science/culture sources overnight and presents a triage page for quick review.

**How it works:**

1. An AI agent researches configured sources (The Register, Ars Technica, HN, Nature, CERN, ArXiv, NVIDIA, AWS, GitHub, Reddit, etc.)
2. Creates ~30 bookmark entries per run in the `picks` context, each with precis and rationale
3. The Picks page (`/tjai/picks/`) presents them grouped by run for triage

**Sources:** Defined in a tjai entry (`picks-sources`, editable via the Sources link on the Picks page). The agent reads this entry before each run to know where to research.

**Picks page (`/tjai/picks/`):**

- Picks grouped by run, reverse chronological
- Each pick: title (link to article, opens new tab), source, precis, rationale
- **Click title** — opens article, marks as viewed/archived (goes grey)
- **Thumbs up/down** — training signal stored in `data.thumbs` for calibrating future runs
- **Keep** — marks as lasting value (green accent), protected from Archive All
- **ReadMe** — adds `:readme` tag, item appears on the ReadMe page
- **Archive All** (per run) — archives all non-kept items in that run
- Stats bar shows total/to-review/kept/archived counts

**ReadMe page (`/tjai/readme/`):**

Reading list of items tagged `:readme`. Shows title, mod date, URL, tags, and precis. Click to read opens the article and removes the tag (item disappears). Remove button for manual removal without reading.

**Archive view:**

Menu item links to Dashboard filtered by `status=archive`, providing full Dashboard filter power (context, kind, tag, search) over archived items.

**Data model (pick entry):**

```python
Entry(
    kind='bookmark', context='picks',
    content='[Article Title](https://example.com/article)',
    data={
        'run': '2026-02-23T02:00:00',
        'precis': 'Summary...',
        'rationale': 'Why this is relevant...',
        'source': 'theregister.com',
        'thumbs': None,     # null | 'up' | 'down'
        'kept': False,
        'archived': False,
        'readme': False,
    }
)
```

**Files:**

- `tjai_app/views.py` — `picks`, `api_picks_data`, `api_picks_update`, `api_picks_archive_run`, `readme_page`, `api_readme_data`, `api_readme_dismiss`
- `tjai_app/templates/tjai_app/picks.html` — Picks triage page
- `tjai_app/templates/tjai_app/readme.html` — ReadMe reading list page

### Action Agent

An always-on daemon that executes automated tasks defined as `kind=action` entries. Each action entry carries its full configuration in the `data` JSON field: trigger type, interval, mechanical script, journal entry template, and AI prompt. The daemon checks for due actions, executes them, and sleeps until the next one is due.

**Architecture:**

The action agent separates mechanical work (scripts) from intelligent work (AI). Each action's pipeline runs in sequence: mechanical script -> journal entry creation -> AI dispatch via `tj agent`. The shared execution logic lives in `tjai_app/action_runner.py`, used by the daemon, CLI, and MCP.

```
supervisord
  └── action-agent (always-on daemon)
        ├── main loop: check due → execute → sleep
        ├── SIGHUP: wake immediately, check all actions
        ├── SIGTERM: graceful shutdown
        ├── heartbeat: SysConfig every cycle
        └── PID: stored in SysConfig for tj wake
```

**Action entry data schema:**

```json
{
  "trigger": "overnight",
  "interval_hours": 24,
  "last_run": 1771713613.75,
  "mechanical_script": "daily_history.py",
  "ai_prompt": "{guidance}\n\nRead .../MM-DD-complete.md. Curate...",
  "journal_entry": {
    "content": "Today in History: {date_str}",
    "tags": "history"
  }
}
```

**CLI commands:**

```bash
tj l actions          # List all action entries (numbered)
tj run 1              # Execute action #1 (from last listing)
tj run daily_history  # Execute by name or content match
tj wake               # Send SIGHUP to daemon (check all now)
```

**MCP tool:** `run_action(entry_id)` — executes a specific action immediately.

**Dashboard:** `tj` status view shows action agent status (running/not running, PID, heartbeat age).

**Management:**

```bash
./deploy/restart_action_agent.sh   # Start/restart via supervisord
tj wake                             # Wake daemon to check due actions
tj l actions                        # See what actions exist
```

**Files:**

- `scripts/action_agent.py` — The daemon (signal handlers, sleep loop, heartbeat)
- `tjai_app/action_runner.py` — Shared execution logic (mechanical, journal, AI, templates)
- `tj/commands/run_action.py` — CLI handlers for `tj run` and `tj wake`
- `deploy/supervisord.conf` — Supervisord configuration
- `deploy/restart_action_agent.sh` — Convenience restart script

**AI dispatch:** Actions that need intelligence use `tj agent` to launch a detached Claude instance. The agent runs on the Claude subscription (not API credits), has MCP tool access, and writes results back to a tjai tracking entry.

### Application Logging

The action agent and related subsystems log to both stdout (for supervisord) and the database (AppLog table) for dashboard visibility.

**How it works:**

- `DbLogHandler` (`tjai_app/db_log_handler.py`) is a Python `logging.Handler` that writes log records to the `AppLog` model
- The `action_runner` module configures a logger with two handlers: `DbLogHandler` (DB) + `StreamHandler` (stdout)
- All action agent output uses structured logging (`logger.info/error`) instead of print statements

**Viewing logs:**

- **Web UI:** `/tjai/agent-log/` — filterable by level (DEBUG/INFO/WARNING/ERROR), auto-refreshes
- **API:** `/tjai/api/agent-log?limit=200&level=ERROR` — JSON endpoint for programmatic access

**AppLog model fields:** `source`, `timestamp`, `level`, `levelname`, `message`, `extra_data` (JSON, optional)

**Files:**

- `tjai_app/db_log_handler.py` — The `DbLogHandler` logging handler
- `tjai_app/models.py` — `AppLog` model
- `tjai_app/views.py` — `agent_log` page and `agent_log_data` API
- `tjai_app/templates/tjai_app/agent_log.html` — Log viewer UI

### System Health Monitoring

A real-time system health dashboard at `/tjai/system/` showing server status with auto-refresh.

**What it monitors:**

- **System** — uptime, load, memory, swap, disk usage
- **PostgreSQL** — connections, cache hit ratio, DB size, tuple activity, top tables
- **tjai** — entry counts by kind, recent activity, agent status (Action Agent, Telegram Bot, Supervisord), action schedules, sync machines
- **Backups** — latest backup date, DB dump size, presence of all expected files (env files, data dir, Apache config). Green if complete and recent, yellow/red if stale or missing files
- **Dropbox** — running status, auto-restart if down
- **Processes** — Apache, CloudWatch agent, action agent, supervisord process counts
- **CloudWatch** — 24h CPU utilization chart, memory/swap/disk trends

**Health status (banner):**

- **Green** — all metrics within normal ranges
- **Yellow** — approaching thresholds (load > 2x CPUs, memory < 20%, disk > 80%, backup > 1 day old)
- **Red** — critical (load > 3x CPUs, memory < 10%, disk > 90%, agents down, no backups)

**How it works:**

`system_health.py` collects all metrics and writes to SysConfig (`system_health_data` as JSON, `system_health_status` as green/yellow/red). The web page fetches via `/tjai/api/system/data` and renders client-side. "Refresh Now" button requests a fresh collection via the action agent.

**Files:**

- `scripts/system_health.py` — Metric collection and health assessment
- `tjai_app/templates/tjai_app/system_health.html` — Dashboard UI
- `tjai_app/views.py` — `system_health` page and API endpoints (`system_data`, `system_refresh`)

### Server Backups

Automated daily backup of all tjai server data to Dropbox.

**What gets backed up:**

| Item | Backup filename | Source |
|------|----------------|--------|
| PostgreSQL database | `tjai-db.sql.gz` | `pg_dump`, gzip compressed |
| Production secrets | `env-www.env` | `/var/www/tjai/.env` |
| Personal API keys | `env-home.env` | `~/.env` |
| Data files | `data/` | `/var/www/tjai/data/` (history files etc.) |
| Apache config | `etaverse.conf` | `/etc/apache2/sites-enabled/etaverse.conf` |

**Destination:** `~/Dropbox/tjai-backups/server/YYYY-MM-DD/` — one directory per day, all kept (no rotation).

**Schedule:** Runs overnight as a tjai action entry (`trigger=overnight`, `interval_hours=24`).

**Health monitoring:** The system health page checks backup freshness, file presence, and DB dump size. Alerts if backups are stale (> 2 days) or missing expected files.

**Files:**

- `scripts/backup.py` — Backup script (pg_dump, file copies, Apache config via sudo)

### Gmail Add-on

A Gmail sidebar add-on that detects calendar invite emails (.ics attachments) and creates tjai journal entries with one click.

**What it does:**
- Contextual trigger fires when viewing an email with .ics attachments
- Parses ICS VEVENT: summary, date/time, location, Zoom URL
- Handles Outlook/Exchange Windows timezone names (WINDOWS_TZ_ map), IANA names (validated via probe), and UTC
- Displays time in Eastern with EST/EDT abbreviation
- Creates journal entry formatted as: `Title [Zoom](url) [Gmail](permalink)`
- Gmail permalink via `GmailThread.getPermalink()` API

**Files:**
- `tjai/gmail_addon/Code.gs` — Apps Script code, manually pasted into the [Apps Script project](https://script.google.com/home/projects/18IPT5WjVYnsecm_j9Sv8LSsgbYi9hDM49Pjxc48rWH9tGNLbaN4Fq4jO/edit)
- `tjai/gmail_addon/appsscript.json` — manifest (OAuth scope: `gmail.readonly`)
- Server endpoint: `api/add-journal` in `tjai_app/views.py` (Bearer token auth)

**Setup:**
1. Create a Google Apps Script project at script.google.com
2. Paste contents of `Code.gs` and `appsscript.json`
3. In `setApiKey()`, replace `REPLACE_WITH_ACTUAL_KEY` with the value from SysConfig `gmail_addon_api_key` (also in `~/.env` as `TJAI_GMAIL_ADDON_API_KEY`)
4. Run `setApiKey` once from the editor
5. Deploy as test deployment (Gmail Add-on type)

**Server endpoint accepts:** `{title, event_timestamp, zoom_url, gmail_url, location}`

### Chrome Extension (tj-getlink)

A Chrome extension for copying markdown links and saving bookmarks to tjai. Source code is in the separate `tj-getlink/` directory in this repo.

**Features:**
- Copy page title + URL as markdown link `[Title](url)`
- Copy with clean URL (strips query params and fragments)
- Save to tjai as a bookmark entry (kind `bookmark`, tagged `chrome`)

**Bookmarking:**
- Clicks "Save to tjai" to POST to `api/add-bookmark` endpoint with Bearer token auth
- API key prompted on first use, stored in `chrome.storage.sync`
- Uses browser tab title as the bookmark title
- Server creates entry with content `[Title](url)`

**Files:**
- `tj-getlink/manifest.json` — Manifest V3, permissions: `activeTab`, `clipboardWrite`, `storage`
- `tj-getlink/popup.html/js/css` — Extension popup UI and logic
- Server endpoint: `api/add-bookmark` in `tjai_app/views.py` (same Bearer token as Gmail Add-on)

**Setup:**
1. In Chrome, go to `chrome://extensions/`, enable Developer Mode
2. Click "Load unpacked" and select the `tj-getlink/` directory
3. Click the extension icon, then "Save to tjai" — enter API key when prompted (same key as Gmail Add-on, from SysConfig `gmail_addon_api_key`)

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
