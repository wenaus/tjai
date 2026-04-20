# Entry Version History

Every content or data change to an entry is automatically versioned via a Django `pre_save` signal. Versions are immutable snapshots that enable document evolution tracking, change detection, and recovery.

## How It Works

- **Automatic**: The `pre_save` signal on `Entry` compares old vs new content/data. If either changed, a snapshot is created before the save.
- **Manual**: `snapshot_entry(entry, changed_by)` can be called explicitly (e.g., before deletion).
- **Sequential numbering**: Each entry's versions are numbered v1, v2, v3... (`version_num` field).
- **Attribution**: `changed_by` records who made the change ('web_ui', 'api_delete', 'autosave', etc.).

## Model: `EntryVersion`

| Field | Type | Description |
|-------|------|-------------|
| `entry` | FK → Entry | The entry this is a version of |
| `version_num` | int | Sequential version number (immutable) |
| `content` | text | Snapshot of entry content at that point |
| `data` | JSON | Snapshot of entry metadata |
| `changed_by` | str | Who triggered the change |
| `timestamp` | float | Unix timestamp of the snapshot |

Table: `entry_versions`. Index on `(entry, -timestamp)`.

## MCP Tool: `get_entry_versions`

```
get_entry_versions(entry_id, version=None, age=None, max_content_length=0)
```

**Retrieve a specific version:**
- `version=3` — version number 3
- `version=-1` — previous version
- `version=-2` — two versions back

**Retrieve by age:**
- `age="24h"` — most recent version at least 24 hours old
- `age="7d"` — most recent version at least 7 days old

**List all versions:**
- Omit both `version` and `age` — returns up to 50 versions (newest first) with truncated content

**Example: detect changes in @Underway**
```
# Get current version
current = get_named_entries(name="Underway")

# Get yesterday's version
old = get_entry_versions(entry_id=current.id, age="24h")

# Compare content to see what changed
```

## Use Cases

- **Activity summary**: Compare @Underway current vs 24h-ago version to identify today's work
- **Research ideation**: Track how living documents evolve — shifts in priorities signal emerging interests
- **Recovery**: Restore previous content after an unwanted `replace_entry_content` (view version, copy content, `replace_entry_content`) — or better, prefer `append_entry_content` to add to an entry without risking a clobber
- **Audit**: See who changed what and when

## UI

- **Entry detail page**: Shows version history with version number, timestamp, changed_by, and content preview. Click a version to load it into the editor for restoration.
- **Versions page** (`/tjai/versions/`): Global reverse-chronological list of recent changes across all entries.

## Signal Implementation

`tjai_app/signals.py` — the `pre_save` signal:
- Skips if entry is new (no `pk` yet)
- Loads the old entry from DB and compares content + data
- Skips autosave operations to avoid version spam
- Uses thread-local `_changed_by` for attribution (set via `set_changed_by()` before save)
