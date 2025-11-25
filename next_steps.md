# Next Steps

## Development Vector Analysis

**Where it's been:**
- Phase 1 (early commits): Core CRUD, basic entry types, contexts/tags
- Phase 2 (recent): Intensive UX polish - DRY refactoring, link colorization, consistent formatting, error prevention
- Phase 3 (latest): Architecture for distribution - output buffering, interactive prompt handling, calendar urgency features

**Where it is NOW:**
The codebase is in an exceptionally clean state. The massive DRY refactoring is complete, UX is polished, the architecture is coherent. You're actively using this daily across desktop + dev server with Claude Code integration.

**Critical constraint identified:**
"Dropbox conflicts proven within 2 days of use" - This is your blocker. The current Dropbox sync works for single-machine, fails for concurrent multi-machine use.

**Where it's pointing:**

The development vector is **unmistakably** pointing toward distributed personal AI integration. Looking at the trajectory:

1. **Personal knowledge base** → Rich metadata (contexts, tags, @names, priorities)
2. **AI integration** → AI guidelines system, Claude Code integration via MCP
3. **Multi-machine use** → Desktop calendar + dev server coding simultaneously
4. **Sync breakdown** → Dropbox conflicts within 48 hours of real use

The next phase is clear from DESIGN.md: **Build the agent infrastructure for real-time sync + MCP server.**

## What to Do Next

### Immediate Priority: **Solve the sync blocker**

The architecture is already designed (DESIGN.md lines 26-76). You need:

1. **Django REST API server** (PostgreSQL backend)
   - Single source of truth
   - Handles all concurrency
   - Runs on EC2 or similar

2. **tjai-agent daemon** (Python)
   - Polls server every 5s for updates
   - Pushes dirty entries to server
   - Merges server changes into local SQLite
   - Detects conflicts → creates conflict entries

3. **MCP server in agent**
   - Exposes tj operations to Claude Code
   - Local access (no latency)
   - Reads synced SQLite cache

### Why This Sequence

Looking at your design decisions:
- You rejected "direct REST per command" (latency)
- You rejected "manual sync" (memory aid can't depend on memory)
- You selected "daemon + transparent sync" (instant local + background sync)

This is the right architecture. The current UX polish phase makes this the perfect time to implement it - the CLI is stable, so you won't be chasing moving targets while building distributed infrastructure.

### Implementation Order

**Week 1-2: Server side**
```
1. Django REST API with PostgreSQL
2. Entry CRUD endpoints
3. Bulk sync endpoint (timestamp-based)
4. Conflict detection logic
```

**Week 3-4: Agent daemon**
```
5. Basic sync loop (poll server, push dirty)
6. Merge logic for incoming changes
7. Conflict entry creation
8. Health checks
```

**Week 5: MCP integration**
```
9. MCP server in agent
10. Expose tj operations (query, create, context)
11. Test with Claude Code
```

**Week 6: Integration & polish**
```
12. Agent lifecycle (install/start/stop)
13. Network outage handling
14. Testing concurrent desktop + dev server workflow
```

### Alternative: MVP Approach

If full server infrastructure feels too heavy initially, there's a simpler MVP:

**Transaction log sync via Dropbox:**
- Keep local SQLite as-is
- Each machine appends changes to `tj_machine_id.log` in Dropbox
- Agent watches for other machines' logs
- Applies foreign changes to local SQLite
- Append-only = no file conflicts
- Still need MCP server locally

This gets you:
- Multi-machine sync (fixes blocker)
- No server infrastructure yet
- MCP integration for Claude Code
- Path to full server later

But given that you already have the architecture designed and you're running Django/REST/ActiveMQ on dev server, I'd recommend going straight to the full architecture.

### Bottom Line

**What to do next:** Build the sync agent + REST server infrastructure.

**Why now:** The CLI is feature-complete and polished. The sync blocker prevents your actual workflow. The architecture is already designed. The codebase is in perfect shape for this next phase.

**Don't do:** More CLI features. The app is complete enough. The blocker is distribution, not functionality.

## Concepts Under Consideration

### Obsidian Integration

[Obsidian](https://obsidian.md/) stores data as plain markdown files in a local directory ("vault"). No proprietary format. Could complement tj's simple text+db entries with rich markdown documents.

**Concept:**
- Two entry types: native tj entries (text in SQLite) and Obsidian entries (pointers to `.md` files)
- `tj l` scans both DB and Obsidian vault, merges by mod time
- Obsidian entries display with `[ob]` marker showing filename and mod date
- `tj s <n>` on Obsidian entry shows file content
- `tj e <n>` on Obsidian entry opens file in Obsidian
- `tj ob <title>` creates new `.md` in vault and opens it
- Context/tags extracted from Obsidian frontmatter (YAML)

**Implementation approach:**
- Pure filesystem scan (no DB entries for Obsidian files) - simpler, no sync issues
- Configure `obsidian_vault_path` in config.json

**Value:** Rich documents (tables, images, complex formatting) in Obsidian. Quick captures in tj. One unified view.
