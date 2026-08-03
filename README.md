# tjai - Personal AI Memory Aid

A personal knowledge system and AI memory aid. TJAI is used primarily through
its web application and authenticated MCP service, with PostgreSQL as the
authoritative store for personal context, projects, and knowledge.

The concise `tj` CLI remains available for fast local and offline capture. Its
per-machine SQLite database and background sync agent are retained secondary
interfaces, not the center of the system architecture.

## Core Principles

- **Authoritative** - PostgreSQL holds the canonical state used by the web app,
  MCP tools, automations, and integrations.
- **Contextual** - contexts and relations organize entries around projects and
  topics.
- **AI-accessible** - authenticated MCP tools expose the same server-backed
  knowledge to supported LLM clients.
- **Concise** - the optional CLI keeps commands short (`tj do Review PRs`,
  `tj c w`).
- **Types vs views** - entry kinds define data; calendars, lists, and other
  views define presentation.

## Capabilities

- **Web application** with entry editing, filtering, search, and specialized pages
- **LLM integration** through a standalone authenticated MCP service
- **AI agents** — overnight news curation (Picks), deep research queue, RSS reader
- **Daily synopsis** — automated journal built from section modules (keeps, git, backups, health, history)
- **Telegram bot** — voice/text AI assistant with calendar reminders and Mini App
- **Add-ons** — Gmail calendar invite capture, Chrome bookmark extension
- **CLI compatibility** with local/offline SQLite capture and authenticated background sync

## Documentation

| Document | Contents |
|----------|----------|
| [Installation & Configuration](docs/configuration.md) | Quick start, database setup, config file, backup/restore |
| [Python Environment](docs/python-environment.md) | Interpreter pin, requirements layout, uv-based venv build (single source of truth) |
| [CLI Reference](docs/cli.md) | Complete command reference for `tj` |
| [Architecture](docs/architecture.md) | Sync design, MCP gateway, design decisions |
| [Sync Agent](TJ_AGENT.md) | `tj_agent` sync daemon: per-machine SQLite, push/pull protocol, conflict handling, daemon lifecycle |
| [Dashboard](docs/dashboard.md) | Web dashboard client: page modes, URL-state model, panels, filtering, search, endpoints |
| [Markdown Rendering](docs/rendering.md) | Server-side render pipeline, linkify policy, Prism code blocks, `text` prose fences |
| [Server & Deployment](docs/server.md) | Production environment, deploy, endpoints, logging, health, backups |
| [MCP Server](docs/mcp.md) | Standalone MCP ASGI service: transport policy, bearer auth, Apache routing, deployment; related read-only Postgres MCP |
| [Action Agent](docs/action-agent.md) | Scheduled task daemon, execution pipeline, action entry schema |
| [Local Maintenance Actions](docs/local-actions.md) | Small machine-local jobs run by the sync agent, separate from EC2 action entries |
| [Agents](docs/agents.md) | Daily synopsis, AI news curation (Picks), autonomous research, RSS reader |
| [Picks Page Curation](docs/picks-curate-page.md) | Curating picks from the page being viewed: browser extension flow, curate-page endpoint, background job |
| [Capcom](docs/capcom.md) | Design for the live notice page: notice feed with read/unread, state tiles and pinned bookmarks, Notice model, polling collectors, source registry |
| [Indico Access](docs/indico-access.md) | Authenticated CERN Indico fetching via a logged-in Chrome session |
| [Browser Offline Cache](docs/offline-cache.md) | Research and generic browser-side offline caching for tjai pages and APIs |
| [Remote Worker Pipeline](docs/remote-workers.md) | Long-poll protocol for offloading inference (e.g. gemma) to a worker on another machine — capability whitelist, claim lifecycle, display contract, troubleshooting |
| [Claude Integration](docs/claude-integration.md) | MCP setup, Claude Code settings, dialog memory, Claude.ai, OAuth |
| [Telegram Bot](docs/telegram.md) | Voice/text assistant, setup, voice commands, Mini App |
| [Add-ons: Gmail & Chrome](docs/addons.md) | Gmail calendar add-on, Chrome bookmark extension |
| [Bulk Import](docs/bulk-import.md) | Importing bookmarks from external sources |
| [Entry Versions](docs/versions.md) | Automatic version history, MCP retrieval, change detection |
