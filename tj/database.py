import atexit
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

# Import consolidated options (must be first for correct parsing)
from tj.options import DEBUG, DB_PATH, debug_time, debug_mark

# --- Constants and Configuration ---
APP_DIR = Path(os.environ.get("TJAI_APP_DIR", Path.home() / ".tjai"))

# Cached values to avoid repeated lookups
_cached_db_path: Optional[Path] = None
_cached_connection: Optional[sqlite3.Connection] = None
_dirs_initialized = False


def get_configured_db_path() -> Path:
    """Get the configured database path (cached)."""
    global _cached_db_path
    if _cached_db_path is not None:
        return _cached_db_path

    start = time.time()
    # Check for --db= override from options
    if DB_PATH:
        _cached_db_path = DB_PATH
    else:
        try:
            from tj.config import get_db_path
            _cached_db_path = get_db_path()
        except ImportError:
            _cached_db_path = APP_DIR / "tjai.db"
    debug_time("get_configured_db_path", start)
    return _cached_db_path


def _ensure_dirs() -> None:
    """Ensure required directories exist (once per session)."""
    global _dirs_initialized
    if _dirs_initialized:
        return

    start = time.time()
    db_path = get_configured_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _dirs_initialized = True
    debug_time("ensure_dirs", start)


class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass


def _close_cached_connection() -> None:
    """Close cached connection at exit."""
    global _cached_connection
    if _cached_connection is not None:
        _cached_connection.close()
        _cached_connection = None


atexit.register(_close_cached_connection)


# --- Database Setup ---
def get_db_connection() -> sqlite3.Connection:
    """Get the cached database connection (single connection per session)."""
    global _cached_connection

    if _cached_connection is not None:
        return _cached_connection

    start = time.time()
    try:
        _ensure_dirs()
        db_path = get_configured_db_path()
        _cached_connection = sqlite3.connect(db_path)
        _cached_connection.row_factory = sqlite3.Row
        debug_time("sqlite3.connect", start)
        return _cached_connection
    except (sqlite3.Error, OSError) as e:
        raise DatabaseError(f"Failed to connect to database: {e}")

def init_db() -> None:
    """Initializes the database schema if it doesn't exist."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Main entries table - core columns + JSON for extensibility
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            content TEXT NOT NULL,
            kind TEXT NOT NULL, -- 'memory', 'bookmark', 'todo', 'journal', 'profile', 'ai', 'list', etc.
            timestamp_created REAL NOT NULL,
            timestamp_modified REAL NOT NULL,
            context TEXT,
            is_dirty INTEGER DEFAULT 1,
            deleted_at REAL, -- Soft delete timestamp
            name TEXT, -- Optional unique name for stable references
            priority INTEGER, -- Priority level (1-5 typical, unrestricted)
            status TEXT, -- Workflow status (done, blocked, waiting, etc.)
            data JSON, -- Extensible data: event_date, links, metadata, etc.
            FOREIGN KEY (parent_id) REFERENCES entries (id)
        );
        """)
        
        # Tags table (many-to-many relationship) - kept for search performance
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            tag_name TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            PRIMARY KEY (tag_name, entry_id),
            FOREIGN KEY (entry_id) REFERENCES entries (id)
        );
        """)
        
        # Create index on tag_name for fast tag queries
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_tag_name ON tags(tag_name);
        """)
        
        # Contexts table - contexts as their own entities
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contexts (
            name TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            timestamp_created REAL NOT NULL,
            timestamp_modified REAL NOT NULL
        );
        """)

        # Sub-notes table (for the 'a' command)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sub_notes (
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp_created REAL NOT NULL,
            data JSON, -- Extensible data for sub-notes
            FOREIGN KEY (parent_id) REFERENCES entries (id)
        );
        """)
        
        # Sync metadata table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            timestamp_updated REAL NOT NULL
        );
        """)
        
        # Machine tracking table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS machines (
            machine_id TEXT PRIMARY KEY,
            hostname TEXT,
            ip_address TEXT,
            last_sync REAL,
            timestamp_created REAL NOT NULL,
            is_active INTEGER DEFAULT 1
        );
        """)

        # Create unique index on (context, name) for named entries
        cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_context_name
        ON entries(context, name) WHERE name IS NOT NULL
        """)

        conn.commit()
    except sqlite3.Error as e:
        raise DatabaseError(f"Failed to initialize database: {e}")
