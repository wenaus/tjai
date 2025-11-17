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
*   **LIST:** `tj l [c|t]`
    *   `l c`: Lists all unique context names, with last entry date and count.
    *   `l t`: Lists all unique tag names, with last entry date and count.
*   **CONTEXT:** `tj =<context>` with optional flags
    *   `tj =tjai`: Switch to/create context (terse name only)
    *   `tj =tjai -t AI app development`: Create with title
    *   `tj =tjai -t AI app -d Personal project notes`: Create with title + description
    *   `tj =0`: Clear active context
    *   `tj c`: Clear active context (with confirmation)
*   **CREATE:**
    *   `tj p <fact>`: Adds a persistent fact to your profile.
    *   `tj <YYYYMMDD> ...`: Creates a new calendar entry.
    *   `tj <url> ...`: Creates a new bookmark.
    *   `tj [d|do|todo] ...`: Creates a new todo item.
    *   `tj <text> ...`: Default; creates a new memory.
    *   *(All creation commands auto-apply current context and can include `:tags`)*.
    *   Inline context: `tj =tjai meeting notes` (switches to tjai, creates entry)
*   **MODIFY:**
    *   `tj . <n> <text>`: Adds a sub-note to item `<n>`.
    *   `tj x <id>`: Deletes an entry by its unique ID.
    *   `tj <n> x`: Deletes item `<n>` from recent entries.
*   **QUERY:** `tj q ...`
    *   `q [b|r|d|p]`: By type: **b**ookmark, **r**emembered, **d**o, **p**rofile.
    *   `q [t|w|m]`: By time: **t**oday, **w**eek, **m**onth.
    *   `q =<context>`: By context.
    *   `q :<tag>`: By tag.
*   **SYNC:** `tj sync`
    *   Forces a manual sync with the remote server.
*   **HELP:** `tj h`
*   Prints a command summary.

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

2.  **Set up `tj` Alias (Recommended):**
    For convenience, add an alias to your shell's startup file e.g. `~/.bashrc`. Add:
    ```bash
    alias tj='~/github/tjrepo/tjai/tj.py'
    ```
    Then `source ~/.bashrc`.

3.  **Run Tests:**
    With the virtual environment active, run the test suite:
    ```bash
    pytest
    ```
