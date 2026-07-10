# Architecture

## System Center

TJAI is a server-backed personal knowledge system. PostgreSQL is the canonical
data store. The web application, MCP tools, automations, Telegram integration,
and server APIs all operate on that shared state.

```text
Browser --------> Django web and APIs -----> PostgreSQL
LLM clients ----> authenticated MCP -------> services/ORM ---> PostgreSQL
Telegram/agents ---------------------------> services/ORM ---> PostgreSQL

tj CLI ---> local SQLite ---> authenticated tj_agent sync ---> PostgreSQL
                         (retained secondary/offline path)

corun-ai ---> local work API ---> remote inference worker
```

The production web application is served by gunicorn on port 8002 behind
Apache. The standalone FastMCP ASGI service runs separately on port 8003 so
long-running LLM traffic cannot consume the web worker pool. Public data and
control-plane endpoints require their designated authentication.

## Primary Interfaces

### Web Application

The web application is the main human interface. It provides entry editing,
search, dashboards, calendars, research views, agent controls, and other
specialized workflows directly against PostgreSQL.

### MCP

The standalone MCP service is the main LLM interface. Its tools use TJAI's
server models and services rather than a local synchronized database. The tool
surface includes entry CRUD, search, contexts, AI guidance, version history,
dialog memory, and knowledge-graph operations.

MCP is provider-neutral infrastructure. Individual client configuration and
authentication details may differ by model vendor or coding assistant.

### Automations And Integrations

Scheduled agents, Telegram, browser integrations, and remote workers are
server-side or authenticated API clients. They share PostgreSQL state with the
web and MCP interfaces.

## Retained CLI And Sync Path

The `tj` CLI predates the web-centered workflow and remains useful for concise
commands and offline capture. Each CLI machine has a local SQLite database. A
persistent `tj_agent` pushes dirty rows and pulls server updates through the
authenticated REST sync API.

This path is supported but secondary. It should stay reliable and simple; it is
not a foundation for new web or LLM features.

### Truth And Conflict Rules

- PostgreSQL is authoritative; SQLite is a per-machine working cache.
- A push includes the server sync time on which the local edits were based.
- The server applies each push batch transactionally.
- A new entry is accepted.
- An existing entry is accepted when the server has not changed since the
  client's baseline, or when the submitted state already matches the server.
- A differing entry changed on the server after the client's baseline is
  rejected. Its tags and subnotes are left untouched.
- The client leaves a rejected local entry dirty and reports the stale entry ID
  in agent status. TJAI does not automatically merge the two states.
- Pulls do not overwrite dirty or newer local rows.

This guard prevents an offline CLI edit from silently replacing newer web or
MCP work without introducing a distributed merge system for a rarely used
path.

## Version History

Content and substantive metadata changes snapshot the displaced entry state.
Version numbers are unique within each entry and allocated under a PostgreSQL
transaction advisory lock, so concurrent writers cannot create duplicate
numbers. See [Entry Versions](versions.md).

## Local State

Numbered CLI mappings such as `tj 3 d` are stored in
`~/.tjai/state.json`. They are intentionally machine-local and are not synced.

## Design Boundaries

- New interactive workflows should normally target the web application.
- New LLM capabilities should normally use the standalone server-backed MCP
  service.
- Shared mutations should live in services or model-level operations that can
  be used consistently by web, MCP, and automations.
- CLI sync maintenance should favor data integrity and compatibility over new
  distributed-system machinery.
