# tjai Architecture & Design Decisions

## Multi-Device Sync Architecture

### Problem
Need multi-device editing with instant local responsiveness and automatic sync, without manual sync commands or accepting stale data.

### Options Considered

**1. Direct REST per command**
- Every tj command calls server API
- Rejected: Adds 50-200ms latency to every command, requires internet

**2. Manual sync (tj sync)**
- User explicitly syncs when switching devices
- Rejected: Memory aid app cannot depend on memory to sync

**3. Background sync per command**
- Each tj command spawns detached sync process
- Rejected: No persistent sync, stale data until next command

**4. Daemon + transparent sync**
- Persistent local agent continuously syncs SQLite ↔ PostgreSQL server via REST
- Selected: See below

### Decision: Persistent Local Agent

**Architecture:**
```
tj command → Local SQLite (instant read/write)
                 ↓
tjai-agent (persistent daemon):
  - Polls/listens for server updates every 5s
  - Pushes dirty entries to server REST API
  - Pulls updates, merges into local SQLite
  - Detects conflicts → creates conflict entries
  - Provides MCP server interface for AI
  - Enables future AI interactions
```

**Truth model:**
- Server PostgreSQL = single source of truth
- Local SQLite = synced cache per device
- Server handles all concurrency (no distributed locking)
- Agent uses REST API only

**Pros:**
- Instant local response (no network latency)
- Always fresh data across devices
- Works offline, syncs when network returns
- Multi-purpose agent (sync + MCP + AI)
- Fits into broader agent infrastructure (ActiveMQ-based)
- Extremely low conflict probability during normal operation

**Cons:**
- Daemon lifecycle management (start, health checks, restart)
- Network outages cause sync delays
- Conflicts possible during extended offline periods
- Additional infrastructure complexity

**Conflict Resolution:**
- Rare case: same entry modified on multiple machines while offline
- Server detects conflict during bulk sync
- Creates conflict entry (Dropbox-style)
- User resolves manually (has all data)

**Implementation Path:**
1. Build Django REST API on EC2 (PostgreSQL backend)
2. Implement tjai-agent (Python daemon)
3. Add agent health check to tj commands
4. Implement MCP server interface in agent
5. Integrate with ActiveMQ agent infrastructure

**Why This Works:**
Personal app with single user, fast internet when available makes conflicts extremely rare. Agent becomes foundation for AI integration (MCP server) and fits existing agent architecture.

## MCP Gateway Architecture

### Hybrid Local/Server Data Model

**Local SQLite (synced):**
- Recent memories, todos, active projects
- Frequently accessed context
- Must work offline
- Fast, essential data

**Server PostgreSQL (query via agent):**
- Music collection metadata
- Photo/document archives
- Email archives
- Historical data
- Large datasets that don't need local copies

### Agent as Intelligent Router

```
LLM → Local MCP (tjai-agent) → Local SQLite (fast queries)
                              → Server REST API (comprehensive data)
                              → Future: other services
```

**Query routing:**
- `tj l =project` → Local SQLite only
- `tj music beatles` → Server query via agent
- `tj find "topic"` → Aggregates local + server results

**MCP tools provided:**
- `search_memories` - searches both local and server
- `query_music_collection` - server-only data
- `get_context` - local, fast access
- `search_documents` - server archives

### Benefits

**Independence from LLM vendor:**
- Works with any LLM supporting local MCP (Claude Desktop, Cursor, etc.)
- No dependence on vendor's remote MCP support
- No per-request charges for remote MCP access
- Complete control over security and authentication

**Gateway abstraction:**
- LLM never knows data is remote
- Agent handles authentication, routing, caching, rate limiting
- Can aggregate multiple remote services through single MCP interface
- Future-proof: works as local or remote MCP when distributed MCP becomes standard

**Scalability:**
- Vast remote datasets accessible without local storage
- Server can host comprehensive "me descriptor" data
- Local cache remains small and fast
- Add new data sources without changing LLM integration
