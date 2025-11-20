# Implementation Notes

Follow these guidelines in app design and implementation. CAVEAT: these are human written and may lag the design and implementation, ie aspects may be out of date. Other doc generally takes priority, in case of uncertainty, ask.

## Purposes

I want a LLM to act as a companion and memory aid in all things, and I want a gatherer and provider of information about me to LLMs so they can serve as a knowledgeable companion. Compensate for my forgetful brain. Be aware of everything I do and keep learning, and not forgetting, about me. Such an app seems not to exist, amazingly, since every human will want one. So I will implement myself. An ensemble of tools to marshal data and make AIs knowledgeable about different aspects of my life and interests. A CLI tool to enter and retrieve information. Server side services for DB with REST API, web UI, vector DB, MCP and other AI interfacing services. My own personal pod and information wrangler, and its connections to AIs. **Currently: local SQLite app with Dropbox sync for multi-device access.**

- memory aid for personal use.
- logger of all things. everything timestamped.
- source of info about me. Structured "me descriptor" to feed to AIs.
- maintain an easily extended profile of things I want AIs to know about me.
- AI behavioral guidelines: instructions for how AI should interact, what to prioritize, guidelines to follow.
- tracks the projects I work on, establishes project contexts.
- functions as a personal dashboard, showing current context, todos, upcoming calendar items.
- gathers, assimilates and presents to AIs my likes and tastes.
- **Future:** maps, geotagged personal place database.
- **Future:** mobile friendly version.
- supports lists: shopping lists, task lists, wish lists, bucket lists, etc. Lists as single entries with sub-items added via '+' syntax.

## Current Architecture (v1.0)

**Local-First with Dropbox Sync**
- Primary data store: Local SQLite database
- Multi-device sync: Dropbox file sync (configurable location via ~/.tjai/config.json)
- Offline-first: Always works locally, no network required
- Data protection: Automatic hourly backups to ~/.tjai/backups (configurable retention)
- Config/state: ~/.tjai/config.json and ~/.tjai/state.json

**What's Working Now:**
- Full CRUD operations (create, read, update, delete)
- Context system (tj =ctx, tj =0)
- Calendar/journal entries (tj j)
- Tags (:tag), priority (p=N), status (s=val)
- Named entries (@name)
- Sub-items (tj .) for hierarchical notes
- Lists (tj +) for simple collections
- AI guidelines (tj ai)
- Entry copying (tj cp)
- Content truncation (configurable)
- Timezone support (tj tz)
- Editor integration (tj e)
- Comprehensive listing/filtering (tj l)

## Planned Architecture (v2.0+)

**Cloud-First Distributed Service**
- Primary data store: Personal cloud server (REST API)
- Local SQLite: Cache/offline copy, not primary storage
- Background sync daemon: Automatic bidirectional sync
- Vector DB/RAG: Augment AIs with personal info
- Web UI: Browser-based interface
- Mobile clients: iOS/Android support

## Testing Philosophy

- No frivolous tests. No mocking. Meaningful tests on a full function system.
- Tests should verify actual behavior of the complete system, not isolated units with mocked dependencies.
- Focus on integration tests that exercise real database operations and command workflows.

## Sub-items vs Lists vs conventional entries

Two distinct mechanisms for organizing related content, sub-items and lists, are foreseen.
However, since editor-based multiline support is now implemented these variants are much less needed.
Sub-items and lists can be created in the editor. The editor treats the body as markdown so
it is easy to make bulleted hierarchical notes or simple lists.

### Sub-items (Hierarchical Entities)
- Sub-items are **actual entities** (entries in the database) with their own IDs, timestamps, and full metadata.
- They are **children** of a parent entry, one level down maximum (no deeper nesting).
- Parent is the **last referenced parent level item** in the conversation/session.
- Created via: `tj . content` (adds sub-item to current parent context)
- Use cases: Follow-up notes, detailed breakdowns, hierarchical note-taking.
- Limitation: **Only 1 level deep** - no grandchildren allowed.

### Lists (Embedded JSON)
- List items are **NOT separate entities** - they're embedded in the parent entry's JSON data field.
- Lightweight mechanism for simple lists like shopping lists, task lists, wish lists.
- Created via: `tj shopping list` (creates list entity), then `tj + milk`, `tj + bread` (adds items to JSON).
- List items have no individual IDs, timestamps, or metadata - they're just strings in an array.
- Use cases: Shopping lists, simple checklists, quick collections where items don't need individual tracking.

**Key distinction**: If the items need to be queried, tagged, timestamped, or treated as independent entries, use sub-items. If you just need a simple collection within a parent, use lists.

## Design and Implementation

### Core Principles
- **Python**: Current stable Python 3.x (not locked to specific version).
- **Conciseness**: Every command should be as short as possible.
- **Smart interpretation**: Auto-detect URLs, dates, times, contexts, tags.
- **Default action is remember**: `tj <text>` creates a memory, no explicit command needed.
- **Numbered references**: Query results numbered for easy operations (tj 3 x, tj 5 a "note").
- **Editor integration**: Full power of $EDITOR when needed (tj e, tj e <n>).

### Data Model
- **Timestamped everything**: Creation and modification timestamps on all entries.
- **UUIDs**: Client-generated to prevent conflicts across devices.
- **Kind field**: String (not enum) for extensibility - memory, todo, profile, ai, calendar, bookmark, list.
- **Flexible schema**: Core columns (id, kind, content, context, timestamp_created, timestamp_modified, is_dirty) + JSON data field for extensibility.
- **Tags**: Inline :tag syntax, tags preserved in content.
- **Contexts**: Project/event-specific (tjai, hawaii2025), not broad categories (work, personal).
- **Named entries**: @name for reusable/updateable entries.
- **Metadata**: Priority (p=N), status (s=val), inline in content or as separate fields.

### Configuration
- Config file: ~/.tjai/config.json
  - db_path: Database location (default ~/Dropbox/Current/tjai.db)
  - backup_path: Backup directory (default ~/Dropbox/Current/tjai_backups)
  - backup_interval_hours: Auto-backup frequency (default 1)
  - recent_entries_hours: Recent list window (default 24)
  - backup_retention_days: Backup retention period (default 14)
  - calendar_default_days: Default calendar view range (default 30)
  - content_truncate_length: Max lines in list/calendar views (default 100)
- State file: ~/.tjai/state.json (current context, last parent ID)

### Backup Strategy
- Automatic: Every command checks if >1hr since last backup, triggers backup if needed.
- Manual: `tj backup` for immediate backup.
- Format: Daily backups (YYYYMMDD.db) in backup_path.
- Retention: Configurable days, default 14.
- Same-day backups overwrite previous (hourly backups OK).

### Calendar/Journal Entries
- Flexible date/time parsing: YYYYMMDD, MM/DD, weekdays (tomorrow, fri), times (14:30, 2pm).
- Copy entries: `tj cp <n> <datetime>` preserves time if not specified.
- Edit behavior:
  - No date: preserves original event date/time.
  - Time only (10:00): preserves date, updates time.
  - Full date/time: updates both.
- Calendar views: `tj c` (default 30 days), `tj c t/w/m` (today/week/month), `tj c N` (N days), `tj c -N` (last N days).

### AI Guidelines
- Universal guidelines: Apply to all AI interactions.
- Context-specific guidelines: Apply when in specific context.
- Query: `tj ai` shows universal + current context, `tj ai =ctx` shows specific context.
- Create: `tj ai <text>` (universal), `tj ai =ctx <text>` (context-specific).

## Command Reference (Current Implementation)

**Create:**
- `tj <text>` - Memory (default)
- `tj do <text>` - Todo
- `tj p <text>` - Profile fact
- `tj ai <text>` - AI guideline
- `tj j <datetime> <text>` - Calendar/journal entry
- `tj <url> <text>` - Bookmark (auto-detected)

**Query/List:**
- `tj l` - All entries
- `tj l [ai|b|do|j|m|p]` - By type
- `tj l [t|w|N]` - By time
- `tj l =ctx :tag p=N s=val` - Composite filters
- `tj l [c|t|@]` - Contexts/tags/named entries
- `tj c [t|w|m|N|-N]` - Calendar view

**Modify:**
- `tj e` - New entry in $EDITOR
- `tj e <n>` - Edit entry in $EDITOR
- `tj s <n|@name>` - Show entry details
- `tj s =ctx` - Show context details
- `tj <n> @name` - Assign name
- `tj <n> p=N` - Set priority
- `tj <n> s=val` - Set status
- `tj t <n> <tag>` - Tag entry
- `tj mv <n> <ctx>` - Move to context
- `tj cp <n> <datetime>` - Copy journal entry
- `tj ^ <n>` - Pin to top (update timestamp)
- `tj d <n|@name>` - Delete

**Context:**
- `tj =ctx` - Switch/create context
- `tj =ctx -t title` - Set context title
- `tj =ctx -t title -d desc` - Set title and description
- `tj =0` - Clear context

**Hierarchy:**
- `tj . <text>` - Add sub-item to last parent
- `tj + <item>` - Add to current list

**System:**
- `tj` - Status dashboard
- `tj hey` - Personal dashboard (todos, calendar)
- `tj config` - Show configuration
- `tj backup` - Manual backup
- `tj dump` - Export as commands
- `tj tz [zone]` - Set/show timezone
- `tj h` - Help

**Metadata (inline):**
- `@name` - Named entry
- `p=N` - Priority
- `s=status` - Status value
- `:tag` - Tag
- `//url` or `[title](url)` - Links
- `-f file` - File input

## Future Enhancements (v2.0+)

**Distributed Architecture:**
- REST API server with proper auth.
- Background sync daemon (tj sys install/start/stop/status).
- Aggressive push: new entries immediately sync.
- Lazy pull: periodic background updates, manual tj sync for immediate refresh.
- Offline detection and graceful degradation.

**Advanced Features:**
- Vector DB/RAG for AI augmentation.
- Web UI for browser access.
- Mobile clients (iOS/Android).
- Geotagged place database.
- External data integration (weather, github, etc).
- Machine tracking (IP, last update time).

**Security:**
- All communication encrypted (HTTPS).
- API key authentication.
- User-friendly auth (minimal friction).

## Available Characters for Commands
- `_` `^` `/` `+` `.`
