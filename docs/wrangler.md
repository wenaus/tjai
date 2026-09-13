# Wrangler — scheduled and on-demand execution

Status: migration in progress. Stages 1–3 are complete — the wrangler runs
the flagged mechanical actions, including all Capcom collection, system
health, the server backup, and the daily products (synopsis, assessment,
workweek assembler); the action agent runs the remainder. Stage 4 is under
way: the System page's health refresh, the assessment dashboard's Rerun and
the assessment backfill are enqueues of their actions — the last two carrying
their target dates — rather than the `system_health_refresh_requested`,
`assessment_gemini_rerun_date` and `assessment_gemini_backfill_all` flags. New actions are created wrangler-owned (`runner: wrangler`).

tjai adopts [wrangle-ai](https://github.com/BNLNPPS/wrangle-ai) as the
execution substrate for scheduled and on-demand work, replacing the action
agent ([action-agent.md](action-agent.md)) progressively. The substrate's
design is specified in wrangle-ai's `docs/scheduler.md`; this document
specifies the tjai consumer and the migration.

## Why

The action agent has four structural defects, each patched repeatedly rather
than removed:

- Liveness and execution share one thread. A long mechanical action starves
  the heartbeat and monitoring raises a false stale alarm (the 2026-08-10
  server-backup incident).
- Dispatched AI work is fire-and-forget. Tracking it back requires sysconfig
  status choreography, tracking-entry-mtime heartbeats, `/proc` scans, stale
  counters with auto-recovery, and subprocess-PID healing.
- On-demand requests travel as sysconfig flags serviced by polling — eight
  distinct flags, each a hand-rolled durable-work queue — because a signal
  from the web tier cannot cross the OS user boundary.
- Force-run mutates schedule state (an injected `scheduled_time` with a
  stashed original) to reuse the scheduler as its execution path.

The wrangle-ai model removes these classes rather than patching them: work is
a durable row, wake is a Postgres `NOTIFY` (which crosses user boundaries),
liveness is a pulse from a loop that never executes anything, and on-demand
work is an enqueue, not a schedule edit. Schedule definitions stay in the
database, UI-editable, with one monitoring surface — the property that ruled
out systemd timers.

## Architecture

One wrangler agent process under supervisord hosts a `Wrangler` and a
`Foreman`:

- **Bullpen** — `PgBullpen` from `wrangle_ai.postgres`, over a
  `wrangle_workers` table in the tjai database (Django migration; an
  unmanaged-read model gives the UI the work history). A tjai subclass hooks
  `mark_failed` to write an AppLog ERROR and emit a Capcom notice, making
  failure surfacing uniform across every worker rather than limited to a
  hand-maintained list of producers.
- **Roster** — `TjaiRoster` over `kind=action` entries. The schedule
  vocabulary (`trigger`, `scheduled_time`, `scheduled_dow`,
  `interval_hours`) is unchanged and stays UI-editable. A materialized
  `next_due` in the action's data makes the claim atomic
  (`FOR UPDATE SKIP LOCKED` on due rows, advance `next_due` in the same
  statement); it is recomputed from the vocabulary whenever the schedule
  fields are edited. The overnight day-back target date is resolved at claim
  time into the worker payload.
- **Bell** — `PgBell` on the tjai database. The web tier enqueues a worker
  and rings with `pg_notify`; the sysconfig wake flags, the 3-second poll,
  and the SIGHUP path are retired.
- **Pulse** — written to sysconfig (`wrangler_heartbeat`, with the in-flight
  count) once per loop pass. Because the loop never executes work, a stale
  pulse means a stopped process; System-page health drops the
  false-stale-during-backup class entirely.

### Handlers

- **`mechanical`** — journal entry, then the action's script list, sequential,
  abort on first failure; runs the existing `action_runner` code as its doer
  logic. Template variables resolve as today.
- **`ai_dispatch`** — launches `tj agent` as a detached, self-completing doer:
  `start_new_session=True`, pid recorded via `record_doer_pid`, and
  `agent_complete.py` writes the outcome to the bullpen row when the agent
  exits. In this mode `tj agent` execs its run-then-complete shell wrapper
  rather than spawning it, so the recorded pid is the doer's for the whole
  run; a pid that exits at launch reads as dead to the bullpen's liveness
  reclaim, which re-runs the worker five minutes in (2026-09-10 to 09-13,
  `daily-history` twice a night). A deploy restarts the wrangler without killing running agents; the
  bullpen's liveness-checked reclaim leaves surviving doers alone. This
  retires the agent-health check, the tracking-entry heartbeat, and the stale
  auto-recovery.
- **`abort`** — kills a named running worker by the pid on its bullpen row
  and marks it failed. Replaces `agent_kill_requested`, with the same
  durability and audit as any other worker.

On-demand reruns and backfills (daily-history, assessments) become enqueued
workers carrying their target date; the backfill chain is a worker that
enqueues its successor on completion. Force-run becomes a plain enqueue.

Failed workers are not auto-retried: the failure lands in AppLog and Capcom,
and the schedule's next firing is the recovery path. The `retry_after`
mechanism is retired with the action agent. If operational experience shows a
class of transient failures that cannot wait for the next firing, retry
becomes a bullpen reclaim policy bounded by the existing `attempts` column.

## Migration

The wrangler runs alongside the action agent. An action moves by setting
`runner: wrangler` in its data: `TjaiRoster` claims only flagged actions, the
action agent's `get_due_actions` skips them, and a move is reverted by
removing the flag. The action agent is frozen — no new capability — and is
retired when nothing unflagged remains.

Stages, ordered small and safe first. Each stage deploys, then is observed
through at least one natural cycle of its actions before the next begins; the
dashboard reads both sysconfig agent state and worker rows during the
transition. Every move is a one-field flag flip until the final stage —
nothing is deleted earlier.

1. **Force-run correctness, then the smallest mechanicals.** For a
   wrangler-owned action, `run_action` must be a plain enqueue plus bell
   ring: the legacy path's injected `scheduled_time` is restored by
   `update_last_run`, which the roster never calls, so the injection would
   stick permanently. With that fixed, flag `rss-fetcher`,
   `codoc-prs-delta`, and `codoc-prs-full` — pure mechanical, idempotent,
   low consequence, and rss's 2-hour cadence gives fast observation.
   (`server-backup` moved first, before this plan: the incident case.)
2. **Monitoring mechanicals.** `system-health` (30 min), then
   `capcom-dispatcher` (10 min, the highest-frequency action) together with
   the first flag-to-enqueue conversion: the tile Update button's
   `capcom_force_run` flag becomes an enqueued worker.
3. **Daily mechanical products.** `daily-synopsis` (journal plus section
   scripts), `workweek-agent`, and `llm-assessment-gemini` (a mechanical
   wrapper) — the morning record, moved once the machinery has quiet time
   behind it; a failure is visible same-day in Capcom.
4. **Remaining on-demand flags.** `system_health_refresh_requested` becomes
   an enqueue; the assessment rerun and backfill flags become enqueued
   workers carrying their target date, the backfill as a self-chaining
   worker (each completion enqueues its successor — the durable-row form of
   its advance-flag-before-launch protocol); `agent_kill_requested` becomes
   the `abort` handler. After this stage the action agent's sysconfig-flag
   servicing is dead code.
5. **AI dispatch.** `agent_complete.py` gains a dual mode — bullpen
   `mark_done`/`mark_failed` when the agent was launched by a wrangler
   worker (keyed by an environment variable carrying the worker id), the
   sysconfig path otherwise — so both worlds work throughout the
   transition. The `ai_dispatch` handler follows the detached
   self-completing doer convention, and actions move one per observed
   overnight: `daily-history` (with its rerun flag), `daily-assessment`,
   `picks-agent`, `ideation-agent`. The agent-health choreography dies
   per-action as each moves. The agent-queue and System pages read worker
   rows for migrated actions in this stage.
6. **The research multimodel cluster.** Its own design pass in this
   document first: each model run a worker, `research_queue` absorbed by
   the bullpen, the remote Mac workers as a remote claimant,
   `heal_research_subprocess_state` replaced by PID-aware reclaim. The
   synthesis fan-in stays tjai application logic; the substrate
   deliberately has no workflow primitives.
7. **Retirement.** AppLog pruning and entry-flood detection become ordinary
   scheduled actions under the roster (today they are daemon-embedded and
   would die with it). Then, with nothing unflagged remaining: the
   action-agent program leaves supervisord, the daemon and its sysconfig
   machinery are deleted, and action-agent.md collapses to a stub pointing
   here.
