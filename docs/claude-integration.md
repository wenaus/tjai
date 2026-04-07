# Claude Integration

## MCP Server

The tjai MCP server endpoint is `https://etaverse.com/tjai/mcp/` (HTTP transport).

Authentication: bearer token. The server checks `Authorization: Bearer <token>`
against the value in `SysConfig` row `mcp_bearer_token`. Same shared-secret
pattern used by tjai's other API endpoints (gmail addon, dialog hooks, etc.).
Requests without a valid token get HTTP 401/403 — `/tjai/mcp/` is not open.

claude.ai connectors are NOT supported. The endpoint is for personal MCP
clients (Claude Code, custom tools) only.

### Token rotation

Generate or rotate the token from a Django shell on the server:

```bash
cd /var/www/tjai
.venv/bin/python manage.py shell -c "
import secrets, time
from tjai_app.models import SysConfig
tok = 'tjai_' + secrets.token_urlsafe(32)
SysConfig.objects.update_or_create(
    key='mcp_bearer_token',
    defaults={'value': tok, 'timestamp_modified': time.time()},
)
print(tok)
"
```

Save the printed value into your local environment as `TJAI_MCP_TOKEN`. The
plaintext is recoverable from the `SysConfig` row at any time (it's a shared
secret, not a hash) — rotate by re-running the snippet.

### Claude Code (CLI)

**Project-level config:** `.mcp.json` in the repo root, using env var expansion
so the secret is not committed:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/",
      "headers": {
        "Authorization": "Bearer ${TJAI_MCP_TOKEN}"
      }
    }
  }
}
```

`TJAI_MCP_TOKEN` must be set in the shell environment that launches Claude Code.

**Global config (CLI):**

```bash
claude mcp add --transport http tjai https://etaverse.com/tjai/mcp/ \
  --header "Authorization: Bearer $TJAI_MCP_TOKEN"
```

## Claude Code Settings

Full `~/.claude/settings.json` with tjai MCP, permissions, and status line:

```json
{
  "mcpServers": {
    "tjai": {
      "type": "http",
      "url": "https://etaverse.com/tjai/mcp/",
      "headers": {
        "Authorization": "Bearer ${TJAI_MCP_TOKEN}"
      }
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

