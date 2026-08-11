# Wrangler — scheduled and on-demand execution

Status: design. Migration not started; the action agent runs everything today.

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
  exits. A deploy restarts the wrangler without killing running agents; the
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

Order:

1. `server-backup` — long-running mechanical, the incident case.
2. Remaining mechanical actions (rss-fetcher, system-health,
   capcom-dispatcher, the codoc pair, daily-synopsis, workweek).
3. The sysconfig on-demand flags, replaced by enqueues from the web views.
4. AI-dispatch actions (daily-history, picks, assessments, ideation).
5. The research multimodel cluster — model fan-out, remote workers,
   synthesis fan-in. The fan-in orchestration stays tjai application logic;
   the substrate deliberately has no workflow primitives.
6. Retire the action agent daemon and its sysconfig machinery.

Each step deploys and observes before the next; the dashboard reads both
sysconfig agent state and worker rows during the transition.
