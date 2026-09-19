# Action Agent System

**The action agent is frozen.** Its successor — durable workers on the
wrangle-ai substrate — and the migration plan are in [wrangler.md](wrangler.md);
actions move there progressively, and no new capability is added here.

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
| `trigger` | Trigger class: `daily`, `periodic`, `overnight` (targets the previous day), or `weekly` |
| `scheduled_time` | HHMM string for daily scheduling (e.g. `0010`), or several comma-separated moments (e.g. `0600,1800`) — the due time is the day's earliest moment not yet consumed by `last_run` |
| `scheduled_dow` | Day of week (`mon`..`sun`); with `scheduled_time`, the action runs weekly on that day |
| `interval_hours` | Fallback interval if no `scheduled_time` |
| `last_run` | Epoch timestamp of last execution |
| `retry_after` | Epoch timestamp of a pending retry after a failed dispatch; overrides the normal schedule |
| `mechanical_script` | Script name or list of scripts to run sequentially |
| `journal_entry` | Config to create a journal entry before scripts run |
| `ai_prompt` | Prompt template for AI dispatch via `tj agent` |
| `model` | Optional model override (default: opus); Codex/GPT model names dispatch via the Codex CLI (default `gpt-5.6-sol`) |
| `effort` | Optional reasoning-effort override for the dispatched agent |
| `timeout` | Optional AI dispatch timeout in seconds |
| `workdir` | Optional working directory for the claude doer (default: the agent's own). `research-agent` uses `/home/admin/github` so the research prompt's local forensics reach every checkout |
| `system_prompt_entry_id` | Entry whose content becomes the agent's system prompt |
| `prompt_is_system_prompt` | If true, `ai_prompt` is used verbatim as the system prompt |
| `result_url` | Output page link shown in the agent-queue UI |
| `progress_log` | `entry_id` of a progress-log entry surfaced in the UI |

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
- **Force-run:** MCP `run_action(entry_id)` (or `action_agent.py --queue <id>`) — moves the action's `scheduled_time` so the daemon's next pass picks it up, then wakes the agent; forced runs use the normal scheduler path
- **Wake agent:** set sysconfig `action_agent_wake_requested` to `1` (polled every 3s)

**Important:** The deploy script schedules the graceful restart automatically; `tj restart-agent` serves manual restarts outside a deploy. Never `supervisorctl restart` — it kills in-progress actions.

### AI Dispatch

Actions needing intelligence use `tj agent` to launch a detached Claude instance. Runs on Claude subscription (not API credits), has MCP tool access, writes results to a tjai tracking entry.

**Subagent cap:** every `tj agent` claude launch passes `--settings` with a PreToolUse hook (`scripts/claude_subagent_cap.py`) that hard-blocks Agent/Task tool calls past `TJAI_MAX_SUBAGENTS` (default 3), counting per session in a flock-guarded `/tmp` file. Prompt-level limits are not enforcement — a 2026-07-15 research run instructed to spawn at most 3 subagents spawned 74 and exhausted host memory. Claude Code has no built-in numeric subagent cap; PreToolUse is the supported enforcement point.

**Background-subagent wait:** Claude Code print mode (2.1.275+) stops waiting for still-running background subagents 600 s after the main turn ends and exits with no report. The launch sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` so the run waits for them; the action's `timeout` (the `timeout` wrapper on the command) remains the bound.

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

The daemon also services sysconfig request flags each pass: `agent_kill_requested` kills zombie Claude/Codex agent processes and tracked research subprocess PIDs; `daily_history_rerun_date` and the assessment rerun/backfill flags (`assessment_rerun_date`, `assessment_gemini_rerun_date`, `assessment_backfill_all`, `assessment_gemini_backfill_all`) launch reruns as background subprocesses. Runaway detection stops an action that has run more than 5 times in 30 minutes by stamping `last_run`, blocking retries until its next scheduled time. Action-agent and system-health AppLog rows older than 7 days are pruned hourly.

### Date Convention

`get_target_date()` returns **today** in the configured timezone. `daily-2026-03-03` is created on March 3 and covers March 3. Actions with `trigger: overnight` are shifted one day back — `execute_action()` targets yesterday, so an overnight action assesses the completed day.

### Files

- `scripts/action_agent.py` — Daemon (signal handlers, sleep loop, heartbeat)
- `tjai_app/action_runner.py` — Shared execution logic (mechanical, journal, AI, templates)
- `tj/commands/run_action.py` — CLI for `tj run`, `tj wake`, `tj restart-agent`
- `deploy/supervisord.conf` — Supervisord configuration



Applications built on this system are documented in [agents.md](agents.md).
