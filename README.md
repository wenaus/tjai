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

## Installation & Quick Start

### 1. Make the script executable

```bash
cd /path/to/tjai
chmod +x tj.py
```

### 2. Set up the `tj` command

Add this bash function to `~/.bashrc`:

```bash
tj() { /path/to/tjai/tj.py "$*"; }
```

Replace `/path/to/tjai` with your actual path. Example:

```bash
tj() { ~/github/tjrepo/tjai/tj.py "$*"; }
```

Reload your shell:

```bash
source ~/.bashrc
```

**Why a function?** Enables natural, unquoted commands: `tj my memory entry` instead of `tj "my memory entry"`

### 3. Verify installation

```bash
tj h  # Show help
tj    # Show status (creates ~/.tjai/tjai.db on first run)
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
    *   `tj =context <text>`: Create entry in specified context (overrides active context)
    *   `tj =0`: Clear active context
    *   `tj =0 <text>`: Create context-free entry
*   **CREATE:**
    *   `tj <text> ...`: Default; creates a new memory.
    *   `tj m <text>`: Creates a memory entry.
    *   `tj do <text>`: Creates a todo item (alias: todo).
    *   `tj p <fact>`: Adds a persistent fact to your profile.
    *   `tj ai <guideline>`: Adds AI behavioral guideline or instruction.
    *   `tj j <date/time> <content>`: Creates calendar entry (tomorrow, mon-sun, HH:MM, mmdd, YYYYMMDD)
    *   `tj <YYYYMMDD> ...`: Creates a new calendar entry.
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
    *   `tj <n> @name`: Assign name to entry.
    *   `tj <n> p=N`: Set priority on entry.
    *   `tj <n> s=status`: Set status on entry.
    *   `tj d <n>` or `tj d @name`: Delete entry (with confirmation).
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

1.  **Make Executable:**
    Run the following command from the `tjai` directory:

    ```bash
    chmod +x tj.py
    ```

    **Note:** No external dependencies required - uses Python standard library only.

2.  **Set up `tj` Function (Recommended):**
    For convenience, add this function to your shell's startup file (e.g., `~/.bashrc` or `~/.zshrc`):

    ```bash
    tj() { ~/github/tjrepo/tjai/tj.py "$*"; }
    ```

    Then `source ~/.bashrc` (or `source ~/.zshrc`).

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
