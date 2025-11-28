# Next Steps

## Current State

- CLI functional for daily use
- Dropbox sync fails with concurrent multi-machine use (conflicts within 48 hours)
- Architecture for sync solution designed in DESIGN.md

## Sync Blocker

The sync problem prevents the intended workflow: desktop for calendar/life management concurrent with dev server for coding with Claude Code.

**Designed solution (DESIGN.md):**
1. Django REST API server (PostgreSQL) - single source of truth
2. tjai-agent daemon - polls server, pushes dirty entries, merges changes
3. MCP server in agent - exposes tj to Claude Code

## Under consideration: Obsidian integration

Complement tj's quick captures with Obsidian's rich markdown documents.

- `tj l` scans both DB and Obsidian vault
- `tj s <n>` on Obsidian entry shows file content
- `tj ob <title>` creates new `.md` in vault
- Context/tags from Obsidian frontmatter
- Pure filesystem scan, no DB entries for Obsidian files
- Configure `obsidian_vault_path` in config.json
