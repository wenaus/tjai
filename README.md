# tjai - Personal AI Memory Aid

A personal AI assistant, companion, and memory aid, operated via a concise CLI (`tj`). Its purpose is to serve as a structured **"me descriptor"** — a single source of truth about your life, projects, and knowledge that provides deep context to AI systems.

Built on an offline-first, distributed architecture: the `tj` client works locally on any machine, syncing to a personal cloud backend via a persistent daemon. Always fast, always available, always in sync.

## Core Principles

- **Concise** — commands as short as possible (`tj do Review PRs`, `tj c w`)
- **Smart** — auto-detects input type (URL → bookmark, date → journal, text → memory)
- **Contextual** — global context groups entries under projects/topics
- **Interactive** — numbered results for quick modification (`tj 3 :urgent`)
- **Types vs Views** — entry kinds (journal, memory, todo) define data; views (calendar, list) define presentation

## Capabilities

- **CLI** with rich entry creation, editing, querying, and metadata management
- **Web dashboard** with filtering, entry detail, and specialized pages
- **AI agents** — overnight news curation (Picks), deep research queue, RSS reader
- **Daily synopsis** — automated journal built from section modules (keeps, git, backups, health, history)
- **Telegram bot** — voice/text AI assistant with calendar reminders and Mini App
- **Claude integration** — MCP server for Claude Code and Claude.ai, cross-session dialog memory
- **Add-ons** — Gmail calendar invite capture, Chrome bookmark extension
- **Multi-device sync** — PostgreSQL (server, source of truth) with local SQLite caches synced via persistent daemon

## Documentation

| Document | Contents |
|----------|----------|
| [Installation & Configuration](docs/configuration.md) | Quick start, database setup, config file, backup/restore |
| [CLI Reference](docs/cli.md) | Complete command reference for `tj` |
| [Architecture](docs/architecture.md) | Sync design, MCP gateway, design decisions |
| [Server & Deployment](docs/server.md) | Production environment, deploy, endpoints, logging, health, backups |
| [Action Agent](docs/action-agent.md) | Scheduled task daemon, execution pipeline, action entry schema |
| [Agents](docs/agents.md) | Daily synopsis, AI news curation (Picks), autonomous research, RSS reader |
| [Browser Offline Cache](docs/offline-cache.md) | Research and generic browser-side offline caching for tjai pages and APIs |
| [Remote Worker Pipeline](docs/remote-workers.md) | Long-poll protocol for offloading inference (e.g. gemma) to a worker on another machine — capability whitelist, claim lifecycle, display contract, troubleshooting |
| [Claude Integration](docs/claude-integration.md) | MCP setup, Claude Code settings, dialog memory, Claude.ai, OAuth |
| [Gemini Integration](docs/gemini-integration.md) | MCP setup, Gemini/Antigravity CLI settings, pre-launch context loading, dialog logging |
| [Telegram Bot](docs/telegram.md) | Voice/text assistant, setup, voice commands, Mini App |
| [Add-ons: Gmail & Chrome](docs/addons.md) | Gmail calendar add-on, Chrome bookmark extension |
| [Bulk Import](docs/bulk-import.md) | Importing bookmarks from external sources |
| [Entry Versions](docs/versions.md) | Automatic version history, MCP retrieval, change detection |
