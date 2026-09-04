# AI Assistant Integration

## MCP Server

The tjai MCP server endpoint is `https://etaverse.com/tjai/mcp/` (HTTP transport).
Apache proxies this path to the standalone `tjai-mcp-asgi.service` FastMCP
process, not the main Django/gunicorn web pool.
See `docs/mcp.md` for server architecture, deployment, and verification.

Authentication: bearer token. The server checks `Authorization: Bearer <token>`
against the value in `SysConfig` row `mcp_bearer_token`. Same shared-secret
pattern used by tjai's other API endpoints (gmail addon, dialog hooks, etc.).
Requests without a valid token get HTTP 401/403 — `/tjai/mcp/` is not open.
MCP is operated as finite JSON POST request/response only. Server-pushed MCP
event streams are intentionally unsupported.

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

The canonical settings file is `tjrepo/computers/common/claude-settings.json`,
symlinked to `~/.claude/settings.json`. tjai-relevant facts:

- The MCP bearer token flows through the repo-level `.mcp.json`
  (`${TJAI_MCP_TOKEN}` env expansion), enabled by
  `enabledMcpjsonServers: ["tjai"]`; the settings file's tjai server entry
  carries only the URL.
- Permissions allow tjai tools via the `mcp__tjai__*` wildcard, with
  `defaultMode: "auto"`.
- The status line command is `python3 ~/.claude/statusline.sh`.

Settings and status line are maintained in `tjrepo/computers/common/`. Symlink them:

```bash
ln -s ~/github/tjrepo/computers/common/claude-settings.json ~/.claude/settings.json
ln -s ~/github/tjrepo/computers/common/claude-statusline.sh ~/.claude/statusline.sh
```

The status line shows machine location, working directory, model, git branch,
context usage, and session duration.

## Captures

Images stashed from mail by the Gmail add-on ([addons.md](addons.md#stash-images-captures)) are memory entries tagged `capture`. Each image line in the entry carries an absolute URL of the form `https://etaverse.com/tjai/capture/<entry uuid>/<file>`, served to a logged-in session or the REST bearer. A session reads an image by fetching it with its bearer and opening the saved file:

```bash
curl -sf -H "Authorization: Bearer $TJAI_API_KEY" \
  https://etaverse.com/tjai/capture/<entry uuid>/01-screenshot.png -o <scratch dir>/01-screenshot.png
```

`get_memories`, `search_entries`, and `get_entry` locate captures; the Captures page at `/tjai/captures/` lists them all.

## Cross-Session Dialog Memory

Claude Code and Codex conversations are recorded into tjai so new sessions on
any machine can load recent dialog context.

### How It Works

```
[Session starts] → SessionStart hook → load.py
  → HTTP GET /api/dialog → fetches recent dialog turns
  → Prints SYSPROMPT.md + formatted dialog → injected into Claude context

[User submits prompt] → UserPromptSubmit hook → record.py / codex_record.py
  → HTTP POST /api/dialog → creates tjai entry with role='user'

[Assistant finishes] → Stop hook → record.py / codex_record.py
  → Extracts new assistant messages from the JSONL transcript
    (resuming from a per-transcript byte offset)
  → HTTP POST /api/dialog → creates tjai entry with role='assistant'
```

The session-start fetch merges turns recorded under this machine's location
name with turns recorded under the `etaverse` pseudo-host (server-side
agents), keeps the most recent N, and skips `[DIAG]` diagnostic entries.

Dialog entries: `kind='memory'`, `tag='ccdialog'`, `is_dirty=0`
(server-only). Dialog writes use `context='co-code'`, meaning
co-development dialog across Claude Code, Codex, and future coding assistants,
with `ccdialog` as the durable technical discriminator.
Uses `Entry.objects.create()` directly (bypasses 60s dedup). Metadata in
`Entry.data` includes `role`, `client`, `model`, `model_provider`,
`reasoning_effort`, `session_id`, `project_path`, and `hostname` when the
recording hook can determine them. Older entries may lack the model fields.
POSTed content starting with `<task-notification>` (research subagent
products) is additionally tagged `research-subagent` and annotated with the
source research action entry.

### Hook Scripts

Located in `computers/common/claude-hooks/`:
- `load.py` — SessionStart (synchronous). Prints SYSPROMPT.md, the
  authoritative local date in America/New_York (overriding Claude Code's
  UTC-derived date context), a mandatory session-start bootstrap directive
  (see below), and dialog history.
- `record.py` — UserPromptSubmit + Stop (async). Records prompts and responses.
- `SYSPROMPT.md` — Static context injected at session start.
- `stop-phrase-guard.sh` — Stop hook that blocks the assistant from stopping
  when its last message matches ownership-dodging, session-quitting, or
  permission-seeking phrases; counts firings by category in SysConfig.

Codex equivalents live in `computers/common/codex-hooks/`:
- `codex_load.py` — SessionStart context and recent dialog injection.
- `codex_record.py` — UserPromptSubmit + Stop dialog recording.
- `codex_sysprompt.md` — Static Codex context injected at session start.
- `git_guard.py` — PreToolUse hook blocking destructive git commands
  (`restore`, `clean`, `revert`) in shell calls.
- `hooks.json` — Codex hook wiring for the above.

### Session-Start Bootstrap Directive

`load.py` emits a per-machine directive instructing the model to call
`mcp__tjai__get_ai_guidance(context=X, location_name=Y, audience="anthropic")` and
`mcp__tjai__get_profile()` before responding to the user's first message,
regardless of message content. Mapping lives in `HOSTNAME_CONTEXTS` inside
`load.py`, keyed by `location_name` from `~/.tjai/config.json` (fallback:
`socket.gethostname()`). Unknown hosts fall back to a general-guidance
directive (no `context` arg) and a stderr warning — the universal rules still
apply, and the warning makes the unmapped host discoverable via
`claude --verbose`. Add an entry to `HOSTNAME_CONTEXTS` to also load
project-specific guidance. The profile call is host-independent and always
included.

Both startup tools return compact, size-bounded pages. The model must follow
`next_offset` with otherwise identical arguments until `complete=true`; stopping
early omits guidance. The bounded response prevents client tool-output limits
from silently dropping entries as the instruction set grows.

AI guidance may declare `data.audiences` to limit loading to model-provider
families. Entries without that field are universal. OpenAI-backed clients use
`openai`; Anthropic-backed clients use `anthropic`. Scheduled agents select the
family from the provider they launch. Omitting `audience` excludes targeted
entries.

`location_name` is the canonical tjai machine ID (not OS hostname). When it
is present in `~/.tjai/config.json`, the hook passes it to `get_ai_guidance`
so the server can append the machine's `<location_name>_details` entry — see
"Per-machine guidance" below. If `location_name` is missing from the config,
the bootstrap directive surfaces that fact to the model so it warns the user;
per-machine guidance will not load until the config is fixed.

Why this exists: CLAUDE.md's "before anything else, call `get_ai_guidance`"
rule was inconsistently honored when the first user message read as trivial
(e.g. "hello"). The directive lives in session-start context — the same
channel as dialog history — is imperative, names the context by name, and
explicitly disarms the "trivial greeting" rationalization. Fires
unconditionally, independent of `TJAI_DIALOG_TURNS` activation.

This is a priming fix, not a harness-enforced fix — if priming proves
insufficient, the next step is to have `load.py` fetch the guidance text
server-side (new REST endpoint mirroring `api_dialog`) and inject it directly.

### Per-machine guidance (`<location_name>_details`)

Machine-specific facts — what's deployed here, local working tree roots,
ingress directories, collaboration axes this host cares about, local quirks —
live in a single entry per machine, named by `location_name` with the suffix
`_details`. These entries complement the universal `ai-machine-identity`
rule (who's who / which host is which) with a "what lives here" inventory
for each specific machine.

Convention:

- `kind='memory'`, `context=None`, `data.entry_id='<location_name>_details'`.
  These are **facts about a machine** (deployed apps, paths, local quirks) —
  not AI behavioral rules — so they are `memory`, not `ai`. Behavioral rules
  (how to act) belong in `kind='ai'` entries like `ai-machine-identity`.
- Name uses the exact literal string from `~/.tjai/config.json`'s
  `location_name` field — capitalization matters. Verified examples:
  `ec2dev_details` (ec2dev), `StudioMax_details` (Mac),
  `swf-testbed_details` (BNL host, location_name=`swf-testbed`, OS hostname
  `pandaserver02`). Any verbal shorthand ("mac", "ec2", "panda") is
  shorthand only and must not leak into entry names.
- Each host authors its own entry locally, using current truth about the
  machine — paths, deployed services, ingress conventions, what work
  typically flows to/from this host and to/from which other hosts.

Delivery: on session start, the hook reads `location_name` from the config
and passes it as `location_name=` to `get_ai_guidance`. The server looks up
the `<location_name>_details` entry and appends it to the returned guidance
list. No extra round-trip; no new tool call.

Missing entry: if the server can't find `<location_name>_details`, it
appends an info-type notice to the result list naming the missing entry and
the recipe to create it. Claude surfaces this to the user so the gap is
closed by authoring the entry — never swallowed silently.

Missing `location_name` in config: the hook includes a warning in the
bootstrap directive so the model tells the user. Per-machine guidance will
not load until `location_name` is added to `~/.tjai/config.json`.

Adding a new machine:

1. Set `location_name` to the desired canonical string in
   `~/.tjai/config.json` on that host.
2. (Optional) Add the `location_name` plus its project-context list to
   `HOSTNAME_CONTEXTS` in `load.py`. Without this, the host loads general
   guidance plus its own `<location_name>_details` but no project-specific
   contexts.
3. From that host, author `<location_name>_details` via `create_entry`
   with the convention above.

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

`load.py` authenticates with `TJAI_API_KEY`; `record.py` authenticates with
`TJAI_GMAIL_ADDON_API_KEY`. Both must be set for dialog load and record.

Full laptop environment (including API keys) is in `computers/laptop/config-files/.env`. On a new Mac:

```bash
ln -s ~/github/tjrepo/computers/laptop/config-files/.env ~/.env
```

### Activation States

- `TJAI_DIALOG_TURNS` unset → notice printed, no API calls
- `TJAI_DIALOG_TURNS=0` → disabled, no API calls
- `TJAI_ACTION_ID` set (action-agent run) → recording disabled
- Hook issues blocking startup → `export TJAI_DIALOG_TURNS=0` bypasses all network activity

All errors print to stderr (`claude --verbose`). Hooks always exit 0. HTTP calls have 5s timeout. Dialog content is stored in full and truncated at 2000 chars on display.
