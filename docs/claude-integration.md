# Claude Integration

## MCP Server

The tjai MCP server endpoint is `https://etaverse.com/tjai/mcp/` (HTTP transport).

### Claude Code (CLI)

**Project-level config:** `.mcp.json` in the repo root auto-configures tjai when Claude Code launches:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/"
    }
  }
}
```

**Global config (CLI):**

```bash
claude mcp add --transport http tjai https://etaverse.com/tjai/mcp/
```

### Claude.ai (Web, Desktop, Mobile)

Full support across all platforms — desktop app, browser, mobile, and voice (create memories hands-free).

**Setup:** Settings → Connectors → Add custom connector → `https://etaverse.com/tjai/mcp`

Authenticates via OAuth 2.1 (Auth0). Once connected, Claude can read calendar, todos, memories, and create entries. AI-created entries auto-tagged `fromai`.

## Claude Code Settings

Full `~/.claude/settings.json` with tjai MCP, permissions, and status line:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/"
    }
  },
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  },
  "permissions": {
    "allow": [
      "Bash(ls:*)", "Bash(wc:*)", "Bash(grep:*)",
      "mcp__tjai__get_server_instructions",
      "mcp__tjai__get_calendar", "mcp__tjai__get_profile",
      "mcp__tjai__get_ai_guidance", "mcp__tjai__list_contexts",
      "mcp__tjai__get_todos", "mcp__tjai__get_memories",
      "mcp__tjai__get_bookmarks", "mcp__tjai__search_entries",
      "mcp__tjai__get_named_entries", "mcp__tjai__get_entry",
      "mcp__tjai__get_entry_by_entry_id", "mcp__tjai__create_entry",
      "mcp__tjai__edit_entry", "mcp__tjai__copy_calendar_entry",
      "mcp__tjai__change_entry_kind", "mcp__tjai__run_action",
      "WebSearch", "WebFetch"
    ],
    "defaultMode": "default"
  },
  "alwaysThinkingEnabled": true
}
```

Settings and status line are maintained in `tjrepo/computers/common/`. Symlink them:

```bash
ln -s ~/github/tjrepo/computers/common/claude-settings.json ~/.claude/settings.json
ln -s ~/github/tjrepo/computers/common/claude-statusline.sh ~/.claude/statusline.sh
```

The status line shows model, cost, context usage, session duration, and working directory.

## Cross-Session Dialog Memory

Every Claude Code conversation is recorded into tjai so new sessions on any machine can load recent dialog context.

### How It Works

```
[Session starts] → SessionStart hook → load.py
  → HTTP GET /api/dialog → fetches recent dialog turns
  → Prints SYSPROMPT.md + formatted dialog → injected into Claude context

[User submits prompt] → UserPromptSubmit hook → record.py (async)
  → HTTP POST /api/dialog → creates tjai entry with role='user'

[Claude finishes] → Stop hook → record.py (async)
  → Extracts last assistant text from JSONL transcript
  → HTTP POST /api/dialog → creates tjai entry with role='assistant'
```

Dialog entries: `kind='memory'`, `context='claude-code'`, `tag='ccdialog'`, `is_dirty=0` (server-only). Uses `Entry.objects.create()` directly (bypasses 60s dedup).

### Hook Scripts

Located in `computers/common/claude-hooks/`:
- `load.py` — SessionStart (synchronous). Fetches dialog, prints SYSPROMPT.md + history.
- `record.py` — UserPromptSubmit + Stop (async). Records prompts and responses.
- `SYSPROMPT.md` — Static context injected at session start.

### Configuration

Hook paths in `claude-settings.json` reference `~/.claude/hooks/`:

```bash
ln -s ~/github/tjrepo/computers/common/claude-hooks ~/.claude/hooks
```

Environment variables (in `~/.env`, sourced by `~/.bash_profile`):

```bash
export TJAI_API_KEY="$TJAI_GMAIL_ADDON_API_KEY"   # Bearer token
export TJAI_DIALOG_TURNS=10                         # turns to load (0=disabled)
# export TJAI_API_URL=https://etaverse.com/tjai    # default
```

Full laptop environment (including API keys) is in `computers/laptop/config-files/.env`. On a new Mac:

```bash
ln -s ~/github/tjrepo/computers/laptop/config-files/.env ~/.env
```

### Activation States

- `TJAI_DIALOG_TURNS` unset → notice printed, no API calls
- `TJAI_DIALOG_TURNS=0` → disabled, no API calls
- Hook issues blocking startup → `export TJAI_DIALOG_TURNS=0` bypasses all network activity

All errors print to stderr (`claude --verbose`). Hooks always exit 0. HTTP calls have 5s timeout. Assistant responses truncated at 4000 chars on record, 2000 on display.

## OAuth 2.1 (Technical Details)

For Claude.ai third-party connector integration via Auth0.

| Setting | Value |
|---------|-------|
| Domain | `dev-yjnmn4q2uqphuam2.us.auth0.com` |
| Client ID | `KDoHUD5L0xydOVJywP5f9DoByTpkeOg9` |
| API Identifier | `https://etaverse.com/tjai/mcp` |
| Callback URL | `https://claude.ai/api/mcp/auth_callback` |

**Auth modes:**
- Claude.ai (web): OAuth 2.1 with PKCE via Auth0
- Claude Code (CLI): Direct HTTP, no auth

**Server env vars:** `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_API_IDENTIFIER` (in `/var/www/tjai/.env`)
