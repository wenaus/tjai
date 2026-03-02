# Action Agent System

**Entry references: when an entry has a human-readable `entry_id` (in `data.entry_id`), ALWAYS use it in URLs instead of the UUID.** Example: `/tjai/entry/daily-history`.

The action agent is a supervised daemon (`scripts/action_agent.py`) that executes scheduled tasks defined as `kind=action` entries. Each action carries its full configuration in the `data` JSON field.

### Architecture

```
supervisord → action_agent.py (always-on daemon)
                ├── loop: get_due_actions() → execute_action() → sleep
                ├── SIGHUP: wake immediately
                ├── sysconfig polling every 3s for web UI wake requests
                └── graceful shutdown on SIGTERM/SIGQUIT
```

### Action Entry Data Schema

| Key | Purpose |
|-----|---------|
| `entry_id` | Human-readable identifier (e.g. `daily-synopsis`) |
| `trigger` | When to run: `overnight`, etc. |
| `scheduled_time` | HHMM string for daily scheduling (e.g. `0010`) |
| `interval_hours` | Fallback interval if no `scheduled_time` |
| `last_run` | Epoch timestamp of last execution |
| `mechanical_script` | Script name or list of scripts to run sequentially |
| `journal_entry` | Config to create a journal entry before scripts run |
| `ai_prompt` | Prompt template for AI dispatch via `tj agent` |
| `model` | Optional model override (default: sonnet) |
| `timeout` | Optional AI dispatch timeout in seconds |

### Execution Pipeline

`execute_action(action)` runs in order:

1. **`create_journal_entry()`** — creates journal entry if `journal_entry` config exists. Must happen first since mechanical scripts may append to it.
2. **`run_mechanical()`** — runs `mechanical_script` (string or list). Scripts run sequentially from `scripts/`. Aborts on first failure.
3. **`dispatch_ai()`** — launches `tj agent` with resolved `ai_prompt`. Async — does not block.
4. **`update_last_run()`** — stamps `last_run` in the action's data.

### CLI Commands

```bash
tj l actions          # List all action entries (numbered)
tj run 1              # Execute action #1 (from last listing)
tj run daily_history  # Execute by name or content match
tj wake               # Send SIGHUP to daemon (check all now)
tj restart-agent      # Graceful restart (after current action completes)
```

### Key Operations

- **Deploy code changes:** `deploy/update_from_dev.sh` then `tj restart-agent`
- **Force-run:** set sysconfig `action_force_run` to action's entry_id, or MCP `run_action(entry_id)`
- **Wake agent:** set sysconfig `action_agent_wake_requested` to `1` (polled every 3s)
- **MCP tool:** `run_action(entry_id)` — executes immediately

**Important:** After deploying changes to agent code (`action_agent.py`, `action_runner.py`, `agent_complete.py`), always `tj restart-agent`. Never `supervisorctl restart` — it kills in-progress actions.

### AI Dispatch

Actions needing intelligence use `tj agent` to launch a detached Claude instance. Runs on Claude subscription (not API credits), has MCP tool access, writes results to a tjai tracking entry.

**XML delimiters in `ai_prompt` templates:** When a prompt templates in variable content (e.g. `{guidance}`, entry content, data), wrap the injected content in XML tags to separate instructions from data. Without delimiters, Claude can confuse injected content with prompt instructions — especially when the injected text itself contains directive-like language.

```
# Good — clear boundary between injected content and instructions
<guidance>
{guidance}
</guidance>

Now do the task...

# Bad — guidance text bleeds into instructions
{guidance}

Now do the task...
```

This applies to any `ai_prompt` that uses template variables. Prompts that tell Claude to fetch its own data via MCP calls (e.g. `get_entry()`, `get_memories()`) don't have this problem since nothing is injected.

### Date Convention

`get_target_date()` returns **today** in the configured timezone. `daily-2026-03-03` is created on March 3 and covers March 3.

### Files

- `scripts/action_agent.py` — Daemon (signal handlers, sleep loop, heartbeat)
- `tjai_app/action_runner.py` — Shared execution logic (mechanical, journal, AI, templates)
- `tj/commands/run_action.py` — CLI for `tj run`, `tj wake`, `tj restart-agent`
- `deploy/supervisord.conf` — Supervisord configuration



Applications built on this system are documented in [agents.md](agents.md).
