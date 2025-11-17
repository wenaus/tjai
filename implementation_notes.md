# Implementation Notes

Follow these guidelines in app design and implementation. CAVEAT: these are human written and may lag the design and implementation, ie aspects may be out of date. Other doc generally takes priority, in case of uncertainty, ask.

## Purposes

I want a LLM to act as a companion and memory aid in all things, and I want a gatherer and provider of information about me to LLMs so they can serve as a knowledgeable companion. Compensate for my forgetful brain. Be aware of everything I do and keep learning, and not forgetting, about me. Such an app seems not to exist, amazingly, since every human will want one. So I will implement myself. An ensemble of tools to marshal data and make AIs knowledgeable about different aspects of my life and interests. A CLI tool to enter and retrieve information. Server side services for DB with REST API, web UI, vector DB, MCP and other AI interfacing services. My own personal pod and information wrangler, and its connections to AIs.

- memory aid for personal use.
- logger of all things. everything timestamped.
- source of info about me. Structured "me descriptor" to feed to AIs. The sort of info that tbl's pods should hold about me.
- maintain an easily extended profile of things I want AIs to know about me.
- tracks the projects I work on, establishes project contexts.
- functions as a personal dashboard, showing current context, todos, upcoming calendar items.
- gathers, assimilates and presents to AIs my likes and tastes, by gathering music collection, books, film/tv, recipes, github repos, etc. 
- maps. They are an obsession of mine. More particularly, places. A proper geotagged personal place database, with notes. Can be integrated with mapping apps later.
- mobile friendly in a later version. Keep the architecture open to mobile clients later. Incorporate mobile enrichment now in the schema and design.
- support lists. shopping lists, task lists, wish lists, bucket lists, etc. Lists as single entries with sub-items added via '+' syntax.

## Testing Philosophy

- No frivolous tests. No mocking. Meaningful tests on a full function system.
- Tests should verify actual behavior of the complete system, not isolated units with mocked dependencies.
- Focus on integration tests that exercise real database operations and command workflows.

## Sub-items vs Lists

Two distinct mechanisms for organizing related content:

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

## Design and implementation

- Python: Current stable Python 3.x (not locked to specific version).

- cloud service. must be distributed. local sqlite for speed and airplane mode.
- REST based. sync service. syncs local db to cloud.
- offline first. local first. always works locally, even if cloud is down.
- agent daemon, a user level system service, manages sync in background. every tj command triggers a sync.
- goal is when I go home from work, I do not leave any updates trapped on my work desktop. they will have been automatically and promptly transparently synced to cloud.
- the agent passively updates from the cloud db, every few minutes. If I want an immediate update I use `tj sync`.
- creates a vector DB/RAG augmenting AIs with my personal info. So that at a LLM prompt interface, the LLM knows everything about me and about my projects.
- every piece of info is timestamped. every one has a unique uuid. the uuid is generated locally, client side. 
- designed so that there is no possibility of clashes between client updates, e.g. from different computers. each entry is unique by construction (uuid + timestamp).
- schema must be highly flexible to incorporate new types of info over time. use json structure for transparent extensibility.
- the essential obvious constantly searched on schema columns should be columns. e.g. timestamp, context, kind, is_dirty. the json is for everything else.
- the entry classification field 'kind' should be a string, not an enum, to allow new types to be added without schema changes. Use 'kind' consistently (not 'type').
- sqlite db and other local materials should be kept in ~/.tjai/ directory.
- written in python. well motivated dependencies are fine.
- command line interface. GUI on the web service side.
- keep track of machines syncing to the cloud service. IP etc. And last update time.
- security: all communication with cloud service must be encrypted (https). authentication via api keys.
- extremely important that auth be as user friendly as possible. no friction.
- This app is fundamentally client-server. for sure the queries are against the server. this is not a local sqlite app. we should start treating it as client server from the beginning. the local sqlite is an addon, not vice versa.
- entering just the 'tj' command should produce a comprehensive dashboard summary, then print the help.
- time zone: US east is the default and start, but it needs to be changeable. we need a 'tj tz zone' command supporting eastern, central, pacific, euro. without zone specified, it reports current zone and lists the options. support also a +/-integer option for relative to UTC.
- *anywhere* an item is shown it should have a number to accept operations. tags too. and contexts.
- available characters: _ ^ / + .

## Client-Server Architecture

Primary Architecture: Cloud-first distributed service

- Primary data store: Personal cloud server (REST API)
- Local SQLite: Cache/offline copy only, not primary storage
- All operations should hit server first, fall back to local cache if offline
- Queries: Server-first, with local fallback for offline scenarios
- Creates: Must be instant (no server latency), write local first, agent syncs to server
- Updates/Deletes: Server operations with local cache updates

## Implementation Priority

1. REST API client layer for server communication
2. HTTP request/response handling with proper error handling
3. Offline detection and graceful degradation
4. Local SQLite as secondary cache system
5. Sync logic to keep cache updated

## Data Flow

- tj create: POST to server, cache locally, confirm success
- tj query: GET from server, update local cache, display results
- tj offline: use local cache, mark as "cached results, may be outdated"
- Background sync: periodic server polls, update local cache

## Performance Requirements

- Additions must not see server latency - they need to be fast
- Local cache first for creates, background agent handles server sync
- Agent daemon is necessary for async server communication

## CLI Design Philosophy

- conciseness is paramount. every command should be as short as possible.
- smart input interpretation. tj detects URLs, dates (YYYYMMDD format), etc automatically.
- tj doesn't conflict with common linux commands (confirmed safe).
- default action is "remember" - tj <text> creates a memory, no explicit command needed.
- tags use : syntax anywhere in input: tj "some text :tag1 :tag2".
- the entry contains the tags as-is, don't remove them from the text.
- use :tagname consistently throughout codebase and docs (not #tags).
- context system: tj =<name> sets context, tj =0 or tj c clears it. context auto-applies to all new entries.
- context definition: tj =context -t title -d description (optional -d)
- inline context: tj =context content (no -t flag) switches context and creates entry in one command.
- contexts are specific projects/events (tjai, hawaii2025, chep2024), not broad categories (work, personal).
- query results are numbered for easy reference: tj 3 a "sub-note", tj 5 x (delete).
- command aliases: d/do/todo for todos, single letters where memorable.

## Command Structure Details

- LIST: tj l c (contexts with usage stats), tj l t (tags with counts and last used date)
- QUERY: tj q [b|r|d|p] (by type), tj q [t|w|m] (time periods), tj q =<name> (by context), tj q :<tag> (by tag)
- CALENDAR: tj 20251225 "Christmas dinner" - YYYYMMDD format auto-detected
- PROFILE: tj p "facts about me" - builds the "me descriptor"
- DASHBOARD: tj hey - personal status, context, todos, external data (weather, github, etc)
- HIERARCHY: tj a for sub-notes (keep simple for now, extensibility planned)
- NUMBERED REFS: tj <n> a <text>, tj <n> x - reference items from last query results
- SYNC: manual tj sync, but background daemon does automatic sync on every command

## Data Management

- everything timestamped automatically
- UUIDs prevent conflicts across machines  
- smart type detection: URLs→bookmarks, YYYYMMDD→calendar, default→memory
- context inheritance: all entries get current context automatically
- extensible schema: fixed columns for searchable fields, JSON for everything else
- local-first: always works offline, sync when possible
- the local DB has to be a true DB. Airplane mode will be very real. Sync the server DB locally, get on the plane.
- we need a 'sync everything locally, I'm going offline' command.
- Needless to say, nothing is more crucial for this app than data protection and backup. In fact, let's implement first backup rigfht nmow. Save in the DB the time of the last backup. every time a command is executed, check if the last backup was more than 1hr ago. if it was, invoke the backup tool. once we have a local agent, it will do the local backup. for now, have the client itself make the backup (print 'Backing up...'). Put the backups in ~/.tjai/backups with the name containing the date, as 20240322, not the time, it is OK for hourly backups to overwrite the previous. Don't purge same-day backups.

## System Integration

- sys command (not service): tj sys install/start/stop/status for daemon management
- completely self-contained within tj command set - no external OS commands needed
- alias setup: user creates 'alias tj=/path/to/tjai/tj.py' 
- aggressive push strategy: new entries immediately trigger sync attempt
- lazy pull: periodic background updates, manual tj sync for immediate refresh
