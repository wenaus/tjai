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
                 last_error: str = None, entries_pending: int = None,
                 sync_interval: int = None) -> None:
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
    if sync_interval is not None:
        status["sync_interval"] = sync_interval

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status))


PUSH_BATCH_SIZE = 500


def push_dirty_entries() -> int:
    """
    Push dirty entries to server in batches.

    Returns total count of entries pushed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get dirty entries
    cursor.execute(
        "SELECT * FROM entries WHERE is_dirty = 1"
    )
    all_entries = []
    for row in cursor.fetchall():
        entry = dict(row)
        # Decode data from JSON string to dict for server
        if entry.get('data') and isinstance(entry['data'], str):
            try:
                entry['data'] = json.loads(entry['data'])
            except json.JSONDecodeError:
                pass
        all_entries.append(entry)

    if not all_entries:
        write_status(entries_pending=0)
        return 0

    machine_id = get_machine_id()
    location_name = get_location_name()
    total_pushed = 0

    # Push in batches
    for batch_start in range(0, len(all_entries), PUSH_BATCH_SIZE):
        entries = all_entries[batch_start:batch_start + PUSH_BATCH_SIZE]
        entry_ids = [e["id"] for e in entries]
        placeholders = ",".join("?" * len(entry_ids))

        # Get contexts referenced by this batch
        context_names = {e["context"] for e in entries if e.get("context")}
        contexts = []
        if context_names:
            ctx_placeholders = ",".join("?" * len(context_names))
            cursor.execute(
                f"SELECT * FROM contexts WHERE name IN ({ctx_placeholders})",
                list(context_names)
            )
            contexts = [dict(row) for row in cursor.fetchall()]

        # Get tags for this batch
        cursor.execute(
            f"SELECT * FROM tags WHERE entry_id IN ({placeholders})",
            entry_ids
        )
        tags = [dict(row) for row in cursor.fetchall()]

        # Get sub_notes for this batch
        cursor.execute(
            f"SELECT * FROM sub_notes WHERE parent_id IN ({placeholders})",
            entry_ids
        )
        sub_notes = []
        for row in cursor.fetchall():
            note = dict(row)
            if note.get('data') and isinstance(note['data'], str):
                try:
                    note['data'] = json.loads(note['data'])
                except json.JSONDecodeError:
                    pass
            sub_notes.append(note)

        response = client.push(
            machine_id=machine_id,
            hostname=location_name,
            entries=entries,
            contexts=contexts,
            tags=tags,
            sub_notes=sub_notes,
        )

        if response.get("status") == "ok":
            # Mark batch as clean
            cursor.execute(
                f"UPDATE entries SET is_dirty = 0 WHERE id IN ({placeholders})",
                entry_ids
            )
            conn.commit()
            total_pushed += len(entries)
            remaining = len(all_entries) - batch_start - len(entries)
            if remaining > 0:
                logger.info(f"Pushed batch of {len(entries)} entries, {remaining} remaining...")
        else:
            logger.error(f"Push batch failed: {response}")
            break

    if total_pushed:
        logger.info(f"Pushed {total_pushed} entries total")
    write_status(last_push=time.time(), entries_pending=len(all_entries) - total_pushed)
    return total_pushed


def _merge_batch(cursor, response) -> int:
    """Merge a single batch of pull response into local DB. Returns entry count."""
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
        # Handle data field - may be dict or already JSON string from server
        data_val = entry.get("data")
        if data_val is not None:
            if isinstance(data_val, str):
                data_str = data_val  # Already JSON string
            else:
                data_str = json.dumps(data_val)  # Dict, encode it
        else:
            data_str = None

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
             entry.get("priority"), entry.get("status"), data_str)
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

    return count


def pull_updates() -> int:
    """
    Pull updates from server and merge into local DB.
    Handles paginated responses, pulling batches until server signals completion.

    Returns (count of entries updated, sysconfig dict).
    """
    machine_id = get_machine_id()
    since = get_last_sync_time()
    after_id = ""
    total_count = 0
    sysconfig = {}

    conn = get_db_connection()
    cursor = conn.cursor()

    batch_num = 0
    while True:
        batch_num += 1
        response = client.pull(machine_id=machine_id, since=since, after_id=after_id)

        if response.get("status") != "ok":
            logger.error(f"Pull batch {batch_num} failed: {response}")
            return 0, sysconfig

        count = _merge_batch(cursor, response)
        total_count += count
        conn.commit()

        sysconfig = response.get("sysconfig", sysconfig)

        entries = response.get("entries", [])
        has_more = response.get("has_more", False)

        if has_more and entries:
            # Advance cursor to last entry in batch (ordered by timestamp_modified, id)
            last = entries[-1]
            since = last["timestamp_modified"]
            after_id = last["id"]
            logger.info(f"Pull batch {batch_num}: {count} entries merged, fetching more...")
        else:
            break

    # Update last sync time only after all batches complete
    server_time = response.get("server_time", time.time())
    set_last_sync_time(server_time)

    if total_count:
        logger.info(f"Pulled {total_count} entries in {batch_num} batch(es)")
    write_status(last_pull=time.time())

    return total_count, sysconfig


def sync_cycle() -> dict:
    """Run one sync cycle: push then pull. Returns sysconfig dict."""
    try:
        push_dirty_entries()
        _count, sysconfig = pull_updates()
        return sysconfig
    except Exception as e:
        logger.error(f"Sync error: {e}")
        write_status(last_error=str(e))
        raise
