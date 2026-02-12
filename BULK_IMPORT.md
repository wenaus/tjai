# Bulk Bookmark Import

Import bookmarks from external sources (Dynalist, Pinboard, browser exports, etc.) into tjai.

## Architecture

```
Desktop                              Server
───────                              ──────
1. Parser                            3. /api/bulk-import endpoint
   (source-specific)                    │
   │                                    ▼
   ▼                                 4. bulk_import/loader.py
2. bulk_import/client.py                (Django ORM + auto-tagger)
   (chunked upload)                     │
        │                               ▼
        └──► POST chunks (100) ────► 5. Entries created with tags
```

## Standard Format

Parsers output, and the server accepts, this format:

```python
{
    "content": "[Title](url)",      # Markdown link (required)
    "tags": ["recipe", "nyer"],     # Explicit tags (optional)
    "timestamp": 1609459200.0,      # Unix timestamp (optional, default: now)
    "context": "places"             # Context name (optional)
}
```

## Quick Start

```bash
# 1. Parse source to JSON
python3 -c "
from bulk_import.parsers.dynalist import parse_dynalist_zip
import json
bm = parse_dynalist_zip('~/Desktop/backup.zip')
json.dump(bm, open('~/Desktop/bookmarks.json', 'w'), indent=2)
print(f'{len(bm)} bookmarks')
"

# 2. Deploy server code
./deploy/update_from_dev.sh

# 3. Upload
export TJAI_API_KEY='your-key'
python3 bulk_import/client.py ~/Desktop/bookmarks.json --source-tag dynalist
```

## Client Options

```
python3 bulk_import/client.py FILE [OPTIONS]

FILE                 JSON file or Dynalist .zip
--source-tag TAG     Tag added to all entries (e.g., 'dynalist')
--chunk-size N       Entries per request (default: 100)
--no-skip-existing   Import duplicates
--dry-run            Parse only, don't upload
--server URL         Override server URL
```

## Writing a Parser

Create `bulk_import/parsers/myformat.py`:

```python
def parse_myformat(filepath, **options):
    """Parse myformat export.

    Returns list of dicts: {content, tags, timestamp, context}
    """
    bookmarks = []
    # ... parse your format ...
    for item in items:
        bookmarks.append({
            'content': f"[{item.title}]({item.url})",
            'tags': item.tags or [],
            'timestamp': item.date.timestamp() if item.date else None,
            'context': None,
        })
    return bookmarks
```

Register in `bulk_import/parsers/__init__.py`:

```python
from .myformat import parse_myformat
```

## Pre-Upload Curation

Before importing, AI can help curate:

1. **Flag stale content** - "Best of 2023" lists, past event pages
2. **Auto-tag untagged** - Learn patterns from tagged entries, propose tags
3. **Review proposals** - Output to txt file, user deletes lines to keep, remaining are processed

Example workflow (from Dynalist import):

```python
# Flag potentially stale entries
for bm in bookmarks:
    if re.search(r'best.*(2024|2023)', title, re.I):
        flag(bm, "Best-of list from past year")

# Learn tag patterns from tagged entries
# domain → tag: cooking.nytimes.com → recipe (93% of cases)
# keyword → tag: "recipe" in title → recipe

# Propose tags for untagged, output for review
# User edits file, then apply approved tags
```

## Server Endpoint

`POST /api/bulk-import`

**Auth:** Bearer token (`gmail_addon_api_key` from SysConfig)

**Request:**

```json
{
    "items": [{"content": "[Title](url)", "tags": ["t1"], "timestamp": 123.0}],
    "source_tag": "dynalist",
    "skip_existing": true
}
```

**Response:**

```json
{
    "imported": 100,
    "skipped": 5,
    "errors": [],
    "auto_tags": {"recipe": 10, "video": 3}
}
```

## Sync After Import

Bulk imports preserve original historical timestamps. Since sync pulls filter by `timestamp_modified > last_sync_time`, imported entries with old timestamps won't appear on other machines automatically. After importing, run a full resync on each machine:

```bash
tj admin agent sync
```

This resets `last_sync_time` to 0 and the paginated pull fetches all entries in batches of 500.

## Auto-Tagging

Server applies `tjai_app/tagger.py` rules:

- `youtube.com` → `video`
- `github.com` → `github`
- `arxiv.org` → `paper`
- "recipe" in title → `recipe`

Source tags (e.g., Dynalist `#tags`) are preserved and combined with auto-tags.

## Files

```
tjai/
├── BULK_IMPORT.md           # This doc
└── bulk_import/
    ├── __init__.py
    ├── loader.py            # Server-side Django loader
    ├── client.py            # Desktop upload client
    └── parsers/
        ├── __init__.py
        └── dynalist.py      # Dynalist .txt/.zip parser
```
