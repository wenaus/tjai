"""Sync logic - push dirty entries, pull updates."""

import json
import logging
import socket
import time
import uuid
from pathlib import Path

from tj.database import get_db_connection, APP_DIR
from tj_agent import client

logger = logging.getLogger(__name__)

MACHINE_ID_FILE = APP_DIR / "machine_id"
STATUS_FILE = APP_DIR / "agent_status.json"


def get_machine_id() -> str:
    """Get or create stable machine ID."""
    if MACHINE_ID_FILE.exists():
        return MACHINE_ID_FILE.read_text().strip()
    machine_id = str(uuid.uuid4())
    MACHINE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    MACHINE_ID_FILE.write_text(machine_id)
    return machine_id


def get_location_name() -> str:
    """Get location name from config."""
    from tj.config import get_location_name as config_get_location_name
    return config_get_location_name()


def get_last_sync_time() -> float:
    """Get last sync timestamp from local DB."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM sync_metadata WHERE key = 'last_sync_time'"
    )
    row = cursor.fetchone()
    return float(row["value"]) if row else 0.0


def set_last_sync_time(timestamp: float) -> None:
    """Store last sync timestamp in local DB."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated)
        VALUES ('last_sync_time', ?, ?)
        """,
        (str(timestamp), time.time())
    )
    conn.commit()


def write_status(last_push: float = None, last_pull: float = None,
                 last_error: str = None, entries_pending: int = None) -> None:
    """Write agent status for tj CLI to read."""
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if last_push is not None:
        status["last_push"] = last_push
    if last_pull is not None:
        status["last_pull"] = last_pull
    if last_error is not None:
        status["last_error"] = last_error
    elif "last_error" in status and (last_push or last_pull):
        status["last_error"] = None  # Clear error on success
    if entries_pending is not None:
        status["entries_pending"] = entries_pending

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status))


def push_dirty_entries() -> int:
    """
    Push all dirty entries to server.

    Returns count of entries pushed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get dirty entries
    cursor.execute(
        "SELECT * FROM entries WHERE is_dirty = 1"
    )
    entries = [dict(row) for row in cursor.fetchall()]

    if not entries:
        write_status(entries_pending=0)
        return 0

    # Get contexts referenced by dirty entries
    context_names = {e["context"] for e in entries if e.get("context")}
    contexts = []
    if context_names:
        placeholders = ",".join("?" * len(context_names))
        cursor.execute(
            f"SELECT * FROM contexts WHERE name IN ({placeholders})",
            list(context_names)
        )
        contexts = [dict(row) for row in cursor.fetchall()]

    # Get tags for dirty entries
    entry_ids = [e["id"] for e in entries]
    placeholders = ",".join("?" * len(entry_ids))
    cursor.execute(
        f"SELECT * FROM tags WHERE entry_id IN ({placeholders})",
        entry_ids
    )
    tags = [dict(row) for row in cursor.fetchall()]

    # Get sub_notes for dirty entries
    cursor.execute(
        f"SELECT * FROM sub_notes WHERE parent_id IN ({placeholders})",
        entry_ids
    )
    sub_notes = [dict(row) for row in cursor.fetchall()]

    # Push to server
    machine_id = get_machine_id()
    location_name = get_location_name()

    response = client.push(
        machine_id=machine_id,
        hostname=location_name,
        entries=entries,
        contexts=contexts,
        tags=tags,
        sub_notes=sub_notes,
    )

    if response.get("status") == "ok":
        # Mark entries as clean
        cursor.execute(
            f"UPDATE entries SET is_dirty = 0 WHERE id IN ({placeholders})",
            entry_ids
        )
        conn.commit()
        logger.info(f"Pushed {len(entries)} entries")
        write_status(last_push=time.time(), entries_pending=0)
        return len(entries)

    return 0


def pull_updates() -> int:
    """
    Pull updates from server and merge into local DB.

    Returns count of entries updated.
    """
    machine_id = get_machine_id()
    since = get_last_sync_time()

    response = client.pull(machine_id=machine_id, since=since)

    if response.get("status") != "ok":
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()
    count = 0

    # Merge contexts
    for ctx in response.get("contexts", []):
        cursor.execute(
            """
            INSERT OR REPLACE INTO contexts (name, title, description, timestamp_created, timestamp_modified)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ctx["name"], ctx.get("title"), ctx.get("description"),
             ctx["timestamp_created"], ctx["timestamp_modified"])
        )

    # Merge entries (skip if local is dirty or newer)
    for entry in response.get("entries", []):
        # Check if local entry exists and should be skipped
        cursor.execute(
            "SELECT is_dirty, timestamp_modified FROM entries WHERE id = ?",
            (entry["id"],)
        )
        local = cursor.fetchone()

        if local:
            if local["is_dirty"] == 1:
                # Local has pending changes, skip server version
                continue
            if local["timestamp_modified"] >= entry["timestamp_modified"]:
                # Local is same or newer, skip
                continue

        # Upsert server version
        cursor.execute(
            """
            INSERT OR REPLACE INTO entries
            (id, parent_id, content, kind, timestamp_created, timestamp_modified,
             context, is_dirty, deleted_at, name, priority, status, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (entry["id"], entry.get("parent_id"), entry["content"], entry["kind"],
             entry["timestamp_created"], entry["timestamp_modified"],
             entry.get("context"), entry.get("deleted_at"), entry.get("name"),
             entry.get("priority"), entry.get("status"),
             json.dumps(entry.get("data")) if entry.get("data") else None)
        )
        count += 1

    # Merge tags for received entries
    for tag in response.get("tags", []):
        cursor.execute(
            "INSERT OR IGNORE INTO tags (tag_name, entry_id) VALUES (?, ?)",
            (tag["tag_name"], tag["entry_id"])
        )

    # Merge sub_notes
    for note in response.get("sub_notes", []):
        cursor.execute(
            """
            INSERT OR REPLACE INTO sub_notes (id, parent_id, content, timestamp_created, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note["id"], note["parent_id"], note["content"],
             note["timestamp_created"],
             json.dumps(note.get("data")) if note.get("data") else None)
        )

    conn.commit()

    # Update last sync time
    server_time = response.get("server_time", time.time())
    set_last_sync_time(server_time)

    if count:
        logger.info(f"Pulled {count} entries")
    write_status(last_pull=time.time())

    return count


def sync_cycle() -> None:
    """Run one sync cycle: push then pull."""
    try:
        push_dirty_entries()
        pull_updates()
    except Exception as e:
        logger.error(f"Sync error: {e}")
        write_status(last_error=str(e))
        raise
