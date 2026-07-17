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

- **Deploy code changes:** `deploy/update_from_dev.sh` — it schedules a
  graceful action-agent restart itself (sysconfig flag; the agent
  finishes any in-progress action, exits, and supervisord restarts it on
  the new code)
- **Force-run:** set sysconfig `action_force_run` to action's entry_id, or MCP `run_action(entry_id)`
- **Wake agent:** set sysconfig `action_agent_wake_requested` to `1` (polled every 3s)
- **MCP tool:** `run_action(entry_id)` — executes immediately

**Important:** The deploy script schedules the graceful restart automatically; `tj restart-agent` serves manual restarts outside a deploy. Never `supervisorctl restart` — it kills in-progress actions.

### AI Dispatch

Actions needing intelligence use `tj agent` to launch a detached Claude instance. Runs on Claude subscription (not API credits), has MCP tool access, writes results to a tjai tracking entry.

**Subagent cap:** every `tj agent` claude launch passes `--settings` with a PreToolUse hook (`scripts/claude_subagent_cap.py`) that hard-blocks Agent/Task tool calls past `TJAI_MAX_SUBAGENTS` (default 3), counting per session in a flock-guarded `/tmp` file. Prompt-level limits are not enforcement — a 2026-07-15 research run instructed to spawn at most 3 subagents spawned 74 and exhausted host memory. Claude Code has no built-in numeric subagent cap; PreToolUse is the supported enforcement point.

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

### Watchdog (disabled)

`scripts/watchdog.py` is an anomaly detector that runs as the `watchdog` action's `mechanical_script` (10-minute interval). Checks: dispatch loops, stale agents, zombie processes, error storms, heartbeat staleness, and resource exhaustion (memory/swap/disk thresholds). Non-OK results are logged to AppLog; full results go to SysConfig (`watchdog_last_results`, `watchdog_status`). On anomaly it sends a capped SES email and triggers the `watchdog-escalation` action, an AI dispatch that diagnoses and recommends without taking destructive actions.

Both action entries (`watchdog`, `watchdog-escalation`) are `status: blocked` — disabled 2026-07-15. During the memory-exhaustion incident of that date the watchdog reported all checks OK minutes before the collapse, could not run during it, and its interrupted dispatch left a stuck `agent_watchdog_status=running` that the agent health check flagged as an error every 30 seconds. To re-enable, set both entries' status to active and add a `timeout` value to the `watchdog` action's data so the agent health check can assess its dispatches.

Independent of the watchdog action, the daemon loop in `action_agent.py` runs its own periodic checks, which remain active:

- **Agent health** (30s) — assesses dispatched agents via tracking-entry activity against the action's `timeout`, falling back to a 1-hour default (warned once) when no timeout is configured or the action entry is missing; auto-recovers `running` status left behind by hard-killed agents. The fallback exists because erroring out instead left a stuck status storming the log every cycle with no self-heal (2026-07-15, watchdog).
- **Entry flood** (5 min) — Jaccard-similarity clustering of recently created entries to catch runaway dispatch loops; alerts via the watchdog email helper.
- **Multimodel subprocess** (30s) — `heal_research_subprocess_state()` marks a research model failed when its subprocess PID is gone, so research runs reach a terminal state and synthesis can proceed.

### Date Convention

`get_target_date()` returns **today** in the configured timezone. `daily-2026-03-03` is created on March 3 and covers March 3.

### Files

- `scripts/action_agent.py` — Daemon (signal handlers, sleep loop, heartbeat)
- `tjai_app/action_runner.py` — Shared execution logic (mechanical, journal, AI, templates)
- `tj/commands/run_action.py` — CLI for `tj run`, `tj wake`, `tj restart-agent`
- `deploy/supervisord.conf` — Supervisord configuration



Applications built on this system are documented in [agents.md](agents.md).
