# Entry Version History

Every content or data change to an entry is automatically versioned via a Django `pre_save` signal. Versions are immutable snapshots that enable document evolution tracking, change detection, and recovery.

## How It Works

- **Automatic**: The `pre_save` signal on `Entry` compares old vs new content/data. If either changed, a snapshot is created before the save. Operational data keys (`last_run`, `retry_count`, `run_status`, and similar run-state fields) are excluded from the data comparison, so run-state churn does not create versions.
- **Manual**: `snapshot_entry(entry, changed_by)` can be called explicitly (e.g., before deletion).
- **Sequential numbering**: Each entry's versions are numbered v1, v2, v3... (`version_num` field). Allocation is serialized per entry with a PostgreSQL transaction advisory lock.
- **Attribution**: `changed_by` records who made the change ('web_ui', 'api_delete', 'autosave', etc.).

MCP content transforms run in a database transaction while holding a row lock.
Append, exact-text replacement, section replacement, and restore therefore
calculate from one locked current state, and the content save plus its version
snapshot commit together.

## Model: `EntryVersion`

| Field | Type | Description |
|-------|------|-------------|
| `entry` | FK → Entry | The entry this is a version of |
| `version_num` | int | Sequential version number (immutable) |
| `content` | text | Snapshot of entry content at that point |
| `data` | JSON | Snapshot of entry metadata |
| `changed_by` | str | Who triggered the change |
| `timestamp` | float | Unix timestamp of the snapshot |

Table: `entry_versions`. Index on `(entry, -timestamp)` and a unique constraint
on `(entry, version_num)`.

## Retention

A daily cron (`scripts/cron/purge_old_versions.py`) trims history: versions older
than 30 days are deleted, but the 10 most recent per entry are always kept
regardless of age. So every entry retains at least its last 10 versions, and a
full 30-day window for anything edited more recently — enough that age-based
comparisons (e.g. "what did this look like 24h ago?") still find a baseline for a
living document that sat idle then was heavily edited in a single session.

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

If no version is that old (the entry's whole retained history is younger than the
requested age), the **oldest available** version is returned instead, with a
`note` field — so callers always get a comparison baseline rather than nothing.
This is what lets the ideation agent diff @Underway even on a day when every
retained version is less than 24h old.

**List all versions:**
- Omit both `version` and `age` — returns `{"versions": [...], "count": N}` with up to 50 versions (newest first) with truncated content

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

- **Entry detail page**: Shows version history with version number, timestamp, changed_by, and content preview. Click a version to load it into the editor for restoration. The history header carries two purge buttons: **Purge** deletes all but the most recent version, and **Purge > 1 week** deletes versions older than seven days. Both keep the most recent version and call `api/entry/<uuid>/purge-versions` (POST, optional `days` parameter).
- **Versions page** (`/tjai/versions/`): Global reverse-chronological list of recent changes across all entries.

## Signal Implementation

`tjai_app/signals.py` — the `pre_save` signal:
- Skips if entry is new (no `pk` yet)
- Loads the old entry from DB and compares content + data
- Skips autosave operations to avoid version spam
- Uses a scoped context variable for attribution that always restores the prior source after saving
- Calls the shared serialized allocator used by explicit snapshots
