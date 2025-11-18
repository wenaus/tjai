# tjai - Your Personal AI Memory Aid

## Purpose

`tjai` is a personal AI assistant, companion, and memory aid, operated via a concise and powerful command-line interface (`tj`). Its ultimate purpose is to serve as a definitive, structured **"me descriptor"** — a single source of truth about your life, projects, and knowledge that can be used to provide deep context to other AI systems.

This project is built on an offline-first, distributed architecture. The `tj` client works locally, syncing to a personal cloud backend, ensuring it's always fast, available, and resilient.

## Core Concepts

*   **Concise:** All commands are designed to be as short as possible.
*   **Smart:** The tool intelligently detects input type (URL, date, text) to perform the right action.
*   **Contextual:** A global "context" can be set to automatically group all subsequent entries under a project or topic.
*   **Timestamped:** Every piece of information is automatically timestamped.
*   **Interactive:** Query results are numbered, allowing for easy modification of entries.

## Command Structure

*   **SYSTEM:** `tj sys [install|uninstall|start|stop|status]`
*   Manages the background sync service.
*   **DASHBOARD:** `tj hey`
    *   Shows a personal dashboard of context, todos, and other evolving information.
*   **LIST:** `tj l [c|t|p|b|d|ai]`
    *   `l c`: Lists all contexts with entry counts.
    *   `l t`: Lists all tags with counts.
    *   `l p/b/d/ai`: Lists profiles/bookmarks/todos/AI guidelines.
*   **CONTEXT:** `tj =<context>` with optional flags
    *   `tj =tjai`: Switch to/create context (terse name only)
    *   `tj =tjai -t AI app development`: Create with title
    *   `tj =tjai -t AI app -d Personal project notes`: Create with title + description
    *   `tj =0`: Clear active context
    *   `tj c`: Clear active context (with confirmation)
*   **CREATE:**
    *   `tj p <fact>`: Adds a persistent fact to your profile.
    *   `tj ai <guideline>`: Adds AI behavioral guideline or instruction.
    *   `tj <YYYYMMDD> ...`: Creates a new calendar entry.
    *   `tj <url> ...`: Creates a new bookmark.
    *   `tj [d|do|todo] ...`: Creates a new todo item.
    *   `tj <text> ...`: Default; creates a new memory.
    *   *(All creation commands auto-apply current context and can include `:tags`)*.
    *   Inline context: `tj =tjai meeting notes` (switches to tjai, creates entry)
    *   Multi-line input: `tj at=20251115/10:00 <<!` then type content, end with `!` on its own line
    *   Timestamp override: `tj at=YYYYMMDD/HH:MM <content>` to set custom creation time
*   **MODIFY:**
    *   `tj . <n> <text>`: Adds a sub-note to item `<n>`.
    *   `tj x <id>`: Deletes an entry by its unique ID.
    *   `tj <n> x`: Deletes item `<n>` from recent entries.
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

The database location is **configurable** and can be stored anywhere you choose. By default, it's stored in `~/.tjai/tjai.db`, but you can configure it to use Dropbox, iCloud, or any other location.

**Check current database location:**

```bash
tj config show
```

**Configuration file location:** `~/.tjai/config.json`

The configuration file is created automatically on first run and includes:

*   `db_path`: Database file location (default: `~/tjai/tjai.db`)
*   `backup_path`: Backup directory location
*   `backup_interval_hours`: How often to auto-backup (default: 1 hour)
*   `recent_entries_hours`: How many hours to include in "recent" queries (default: 24)

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

## Development Setup

To set up your development environment and run tests:

1.  **Initialize and Activate Virtual Environment, Install Dependencies, and Make Executable:**
    Run the following commands from the `tjai` directory. This creates a virtual environment, activates it, installs all necessary packages, and makes the main script executable.

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -r requirements-dev.txt
    chmod +x tj.py
    ```

    *Remember to activate the virtual environment (`source .venv/bin/activate`) in each new terminal session where you want to work on `tjai`.*

    **For AI assistants:** Venv doesn't persist between shell commands. Chain activation: `cd tjai && source .venv/bin/activate && python3 test.py`

2.  **Set up `tj` Alias (Recommended):**
    For convenience, add an alias to your shell's startup file e.g. `~/.bashrc`. Add:

    ```bash
    alias tj='~/github/tjrepo/tjai/tj.py'
    ```

    Then `source ~/.bashrc`.

3.  **Run Tests:**
    With the virtual environment active, run the test suite:

    ```bash
    python3 test.py
    ```

### Testing Heredoc Input

When testing multi-line heredoc input from the command line, you need to quote `<<!` so the shell passes it as a literal argument, then use shell heredoc syntax to provide stdin:

```bash
./tj.py at=20250101/00:00 '<<!' <<'END'
Multi-line content here
More lines
!
END
```

This works because:
- The quoted `'<<!'` is passed as an argument to tj.py
- The shell heredoc (`<<'END'...END`) provides stdin to the script
- tj.py sees `<<!` in argv and reads from stdin until it finds `!` on its own line
