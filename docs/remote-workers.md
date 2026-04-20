# Remote Worker Pipeline

The remote-worker pipeline lets the tjai server hand inference work to a worker process running on another machine — typically `tj_agent` running on Torre's Mac Studio with a local ollama instance. This is how the `gemma` model contributes to the multi-model research pipeline: the prompt is built on the server, the inference runs on the Mac, and the result is POSTed back.

The same protocol can in principle dispatch any work item that has a prompt and a string result.

## Why this exists

Cloud API models (Claude, Gemini) are dispatched directly from the EC2 server. Open models live on Torre's local hardware — the Mac Studio has GPU and large enough RAM to run `gemma3` via ollama; the EC2 server has neither. The remote-worker protocol is the bridge that lets the server hand inference to that hardware without giving up control of the prompt or the result entry.

The design is **server-driven, worker-pulled**:
- The server *stages* a work item with a target capability name (e.g. `gemma4`).
- A worker advertises which capabilities it can handle and **long-polls** the server.
- When the server has matching staged work, it atomically claims it for that worker and returns the prompt.
- The worker runs inference and POSTs the result back.

There is no inbound connection to the worker, no fixed worker registry, and no agreement on schedule. A worker can come and go; while it's away, work just stays staged.

## Trust model

`/api/worker/poll` and `/api/worker/result` are `@csrf_exempt` and have **no authentication** — anyone who can reach them can claim staged work and POST a result that becomes the topic's content. This is intentional and matches the existing `sync_push`/`sync_pull` pattern: tjai assumes a trusted network and treats the server URL itself as the secret. If the server is ever exposed beyond Torre's machines, the first hardening step is a bearer token on these four endpoints; nothing else in the protocol depends on the missing auth.

## Components

| Layer | File | Role |
|---|---|---|
| Dispatcher | `tjai_app/action_runner.py` `_dispatch_research_3way` | When a research topic is submitted, creates per-model sub-entries and stages remote-worker models (gemma, qwen) via the `REMOTE_WORKER_MODELS` mapping |
| Endpoint (poll) | `tjai_app/views.py` `worker_poll`, `_claim_worker_entry` | Long-polls for matching work, atomically claims under transaction, returns prompt |
| Endpoint (result) | `tjai_app/views.py` `worker_result` | Receives result, finalizes the sub-entry, calls `research_model_complete` |
| Per-completion hook | `tjai_app/action_runner.py` `research_model_complete` | Updates base entry's `{model}_status`, triggers synthesis once all dispatched models are done |
| Worker (Mac) | `tj_agent/worker.py` | Async loop that polls, runs an agentic chat loop against local ollama (with tools when MCP dispatcher is up), POSTs result, also POSTs `received`/`completed` events to `/api/log` (source `worker-mac`) |
| MCP tool dispatcher (Mac, R&D) | `tj_agent/mcp_tool_dispatcher.py` `McpToolDispatcher` | Spawns local MCP servers as stdio subprocesses (lxr-mcp-server, github-mcp-server), holds long-lived `mcp.ClientSession`s, advertises the union of their tools to ollama, routes `call_tool` dispatch back to the owning server. Optional — failures degrade to tool-less single-shot inference. |
| Worker thread starter | `tj_agent/daemon.py` `run_forever` | Spawns the worker thread (which itself wraps an asyncio loop) alongside the sync loop on `tj_agent` startup |
| Display | `tjai_app/views.py` `api_research_data` + `tjai_app/templates/tjai_app/research.html` banner block | Shows the system state on `/tjai/research/` with three named, non-overlapping facts |

## Wire protocol

### `GET /api/worker/poll`

Long-poll for work matching one or more capabilities. The server holds the request for up to `WORKER_POLL_HOLD_SECONDS` and returns either a single work item or `null`.

| Param | Type | Required | Notes |
|---|---|---|---|
| `machine_id` | string | yes | Stable per-worker UUID. The server uses it for heartbeat tracking and free-capacity reset. |
| `capabilities` | string | yes | Comma-separated capability names. Each must be in `WORKER_CAPABILITIES` or the request is rejected with `400`. |

Responses:

```json
// 200 — work available
{"status": "ok", "work": {
  "entry_id":      "<sub-entry uuid>",
  "work_type":     "research" | "codoc" | "generic",
  "model":         "<capability name, e.g. gemma4>",
  "prompt":        "<full prompt text>",
  "timeout_sec":   <int>,
  "base_entry_id": "<base research entry_id, or null>"
}}
// work_type is derived server-side from the staged entry's data.source:
//   'multimodel' → 'research'   (research dispatcher sub-entries)
//   'corun-ai'   → 'codoc'      (api/work/submit from corun-ai)
//   anything else → 'generic'   (other external submitters)
// It is informational on the wire — the Mac worker labels logs with it
// but the agent loop runs identically regardless.

// 200 — hold expired with no matching work
{"status": "ok", "work": null}

// 400 — bad capability or missing field
{"error": "unknown capabilities: ['nope']. known: ['gemma4']"}
{"error": "machine_id required"}
{"error": "capabilities required"}
```

### `POST /api/worker/result`

Report the outcome of a previously claimed work item.

```json
// request
{
  "machine_id":   "<same uuid as the poll that claimed it>",
  "entry_id":     "<sub-entry uuid from the poll response>",
  "status":       "done" | "failed",
  "result":       "<inference output, on success>",
  "error":        "<error text, on failure>",
  "duration_sec": <int>
}
```

Responses: `200 {"status": "ok"}` on success; `400` on missing/invalid fields; `404` if the entry no longer exists. The endpoint **accepts the result even if the worker isn't the recorded claim holder** — only a warning is logged. Rationale: the work was done; refusing it would just discard real output. (See Failure modes for when this matters.)

### External submission API

The same staging/poll/result pipeline can also be driven by **external apps** that just want to offload a single inference call to a remote worker. Today this is used by `corun-ai` to send `gemma4`/`gemma4-fast` jobs from codoc; tomorrow anything else with a prompt and a string result can use it. The dispatcher and worker side are unchanged — the new endpoints just create and inspect entries that look identical to the ones the research dispatcher stages.

#### `POST /api/work/submit`

Stage a unit of work for the next worker that polls for the matching capability.

```json
// request
{
  "capability":   "gemma4" | "gemma4-fast",
  "prompt":       "<full prompt text>",
  "timeout_sec":  1800,                   // optional, default 1800
  "source":       "corun-ai",             // optional caller identifier
  "label":        "codoc:job-42:p3"       // optional caller's job label
}

// 200 — staged
{"status": "ok", "entry_id": "<uuid>"}

// 400 — bad capability or missing field
{"error": "unknown capability: 'gemmax'. known: ['gemma4', 'gemma4-fast']"}
```

The endpoint creates an `Entry(kind='memory', context='tjai', status='active')` with `data.worker_target`, `data.worker_prompt`, `data.worker_staged_at`, `data.worker_timeout_sec`, and the optional `source`/`external_label` fields. Because the Entry has the same `worker_target` shape that the research dispatcher uses, it is picked up by `_claim_worker_entry` on the very next matching poll — there is no separate scheduler.

#### `GET /api/work/result/<entry_uuid>`

Poll for the current state of an entry created via `/api/work/submit`. Callers should poll on a short cadence (1–2s) until `status` is `done` or `failed`.

```json
{
  "status":         "queued" | "running" | "done" | "failed",
  "result":         "<inference output>",   // present when status=done
  "error":          "<error text>",         // present when status=failed
  "duration_sec":   <int>,
  "claimed_by":     "<machine_id>",         // present once claimed
  "claimed_at_ago": <seconds>,              // present once claimed
  "staged_at_ago":  <seconds>,
  "label":          "<external_label>"
}
```

Status mapping:

| Entry state | Returned status |
|---|---|
| `entry.status='active'` and no `worker_claimed_by` | `queued` |
| `entry.status='active'` and `worker_claimed_by` set | `running` |
| `entry.status='done'` | `done` |
| `entry.status='blocked'` | `failed` |

Returns `404` if the entry does not exist (or has already been soft-deleted via `DELETE`).

The `result`/`error` fields read from `data.worker_result` / `data.worker_error`, which `worker_result` writes alongside the existing `entry.content` rewrite. This avoids the need for callers to parse `content`, which for research entries is prefixed with the topic line.

#### `DELETE /api/work/result/<entry_uuid>`

Soft-delete the entry. Callers should issue this **after** successfully retrieving a `done` or `failed` result, so external work entries do not accumulate in the tjai context. Sets `Entry.deleted_at` — leaves the row in place for audit. Returns `200 {"status": "ok"}` or `404`.

### Constants

| Symbol | Value | Defined in | Meaning |
|---|---|---|---|
| `WORKER_POLL_HOLD_SECONDS` | 50 | `tjai_app/views.py` | Server holds the long-poll for up to this long. Must stay under gunicorn's `--timeout` (120s). |
| `WORKER_POLL_INTERVAL` | 2 | `tjai_app/views.py` | DB recheck frequency inside the hold loop. |
| `WORKER_CLAIM_STALE_SECONDS` | 7200 (2h) | `tjai_app/views.py` | A claim older than this can be auto-reclaimed by any poll. Must comfortably exceed the longest legitimate single work-item runtime (not the longest single ollama call) — with the Mac-side agent loop, one work item can be many ollama turns. |
| `WORKER_CAPABILITIES` | `{'gemma4', 'gemma4-fast', 'qwen'}` | `tjai_app/views.py` | Capability whitelist. Edit this to add a new capability. |
| `POLL_CLIENT_TIMEOUT` | 70 | `tj_agent/worker.py` | Worker socket timeout. Must exceed `WORKER_POLL_HOLD_SECONDS` (the 20s margin covers round-trip + safety). |
| `POLL_BACKOFF_INITIAL` / `MAX` | 1.0 / 60.0 | `tj_agent/worker.py` | Exponential backoff between failed polls. |
| `DEFAULT_INFERENCE_TIMEOUT` | 3600 | `tj_agent/worker.py` | Worker's local per-call timeout on each ollama request (60 min), overridden by `work.timeout_sec`. The agent loop has no separate wall-clock cap — it terminates naturally when the model emits no further tool_calls. |
| `TJAI_LOG_SOURCE` | `'worker-mac'` | `tj_agent/worker.py` | `source` field used when the worker POSTs `received`/`completed`/`failed` events to `/api/log`. Distinct from the server-side `'worker'` events. |

The "no poll for longer than 180s" disconnected threshold is a function-local value inside `api_research_data`, not a top-level constant.

## State surfaces

There are five independent pieces of state. They have orthogonal lifecycles and **must never be conflated in display text** — that is the failure mode that produced the "Agent: idle · 1 active topic · gemma in-progress · worker polling" contradictions of 2026-04-06.

| # | Where it lives | Purpose | Values |
|---|---|---|---|
| L1 | `SysConfig['agent_research-agent_status']` | The **local research-agent process** — the orchestrator that builds prompts, dispatches Claude/Gemini directly, stages gemma, and writes synthesis. | `idle`, `running`, `failed` |
| L2 | base entry's `Entry.status` | Whether the **topic as a whole** is finalized | `pending`, `active`, `done`, `blocked` |
| L3 | base entry's `data.{model}_status` for each model in `RESEARCH_MODELS = ('claude','gemini','gemma','qwen')` | Per-model lifecycle for this topic | `None`, `staged`, `active`, `done`, `failed`, `rerun` (legacy entries may still carry `blocked`; treat as `failed`) |
| L4 | sub-entry's `data.worker_*` (only present while remote work is in flight) | Per-claim tracking | `worker_target`, `worker_staged_at`, `worker_claimed_at`, `worker_claimed_by`, `worker_prompt`, `worker_timeout_sec` |
| L5 | `SysConfig['worker_capability_{cap}_lastpoll']` → `{machine_id, ts}` | "Has any worker polled for this capability recently, and who" | JSON: machine_id, ts |

The local research-agent process (L1) being `idle` while a remote worker is mid-inference (L4 has a fresh claim) is **correct and intentional** — once the local agent has staged the gemma work and dispatched Claude/Gemini, it has nothing more to do until results arrive. Display text that reads "Agent: idle" alone is misleading because it conflates L1 with the system as a whole.

### L3 transitions for a remote-worker model

| From | To | Trigger | Where |
|---|---|---|---|
| `None` | `staged` | Dispatcher stages work (writes `worker_target`, `worker_prompt`, `worker_staged_at` on a sub-entry) | `_dispatch_research_3way` |
| `staged` | `active` | A worker poll claims the sub-entry | `_claim_worker_entry` |
| `active` | `staged` | Free-capacity reset (the same worker polls again, signaling it has no in-flight work) | `worker_poll` |
| `active` | `done` | Worker POSTs `status='done'` | `worker_result` → `research_model_complete` |
| `active` | `failed` | Worker POSTs `status='failed'` | `worker_result` |
| `done` / `failed` | `rerun` | User clicks "Rerun selections" with this model checked | `api_research_rerun_models` |
| `rerun` | `staged` | Next dispatch loop picks it up | `_dispatch_research_3way` |

For local-dispatch models (claude, gemini) the same L3 field is set directly without any `staged` phase.

### Sub-entry status vs L3

A sub-entry's `Entry.status` is set to `'active'` at staging time and stays `'active'` through both the unclaimed-staged window AND the in-flight inference window. **The two are distinguished only by the presence of `worker_claimed_at` on the sub-entry**, not by the row's `status` field. The `staged`/`active` distinction lives on the *base* entry's `data.{model}_status`. A reader inspecting the DB who sees `Entry.status='active'` on a sub-entry must look at `data.worker_claimed_at` to know whether a worker has actually claimed it.

## Lifecycle of a remote-worker job

The diagram below traces a single research topic where gemma is one of the dispatched models.

```
0. User submits "research-foo" via the research page.
   The research-agent action runs _dispatch_research_3way().

1. `action_runner.py` `_dispatch_research_3way`
   For model='gemma':
     - Create sub-entry research-foo-gemma with data:
         worker_target='gemma4'
         worker_prompt=build_research_prompt(topic_text)
         worker_staged_at=now
         base_entry_id='research-foo'
         model='gemma'
     - sub-entry.status = 'active'
     - base.gemma_status = 'staged'         ← L3
     (claude/gemini are dispatched in parallel via different paths)

2. Mac worker (tj_agent thread) polls:
   GET /api/worker/poll?machine_id=ed8e0e3a&capabilities=gemma4
   This is a *long poll* — the server holds the request for up to
   WORKER_POLL_HOLD_SECONDS (50s) waiting for matching work.

3. `views.py` `worker_poll`
   a. Validate capabilities are in WORKER_CAPABILITIES whitelist
      (unknown ⇒ HTTP 400, never recorded as a fake worker)
   b. Update Machine row (heartbeat)
   c. Update SysConfig 'worker_capability_gemma4_lastpoll'  ← L5
      = {machine_id: ed8e0e3a, ts: now}
   d. Free-capacity reset (see below)
   e. Loop calling _claim_worker_entry every WORKER_POLL_INTERVAL (2s)
      until claim succeeds or hold deadline expires

4. `views.py` `_claim_worker_entry`
   For each capability:
     - Query Entry where data.worker_target=cap, status='active'
     - For each candidate, in transaction.atomic():
         * select_for_update the row
         * Re-check it isn't already claimed (or that the prior claim
           is older than WORKER_CLAIM_STALE_SECONDS = 2h)
         * Set worker_claimed_by = machine_id           ← L4
                worker_claimed_at = now
         * Save the sub-entry
         * If base_entry_id and model are set:
             upgrade base.{model}_status from 'staged' to 'active'  ← L3
         * return the locked entry
   (Whole block atomic — if base update fails, claim rolls back.)

5. worker_poll returns the work payload (see Wire protocol § GET
   /api/worker/poll for the exact shape) and writes an AppLog row.

6. Mac worker (`tj_agent/worker.py` `_process_work_async`)
   - POST `received` event to /api/log (source 'worker-mac')
   - Run an agentic chat loop against local ollama:
       messages = [{role: user, content: prompt}]
       while True:
         response = ollama /api/chat(messages, tools=dispatcher.tools_for_ollama())
         messages.append(response.message)
         if not response.message.tool_calls:
            final_text = response.message.content
            break
         for each tool_call:
            result = dispatcher.call_tool(name, args)  # routed to the owning MCP server
            messages.append({role: tool, name, content: result})
   - The loop has no turn cap. For research-style single-shot prompts
     and any other prompt the model answers without invoking tools, it
     terminates after turn 1 with the assistant's content — same
     observable result as the pre-agent single-shot path. When tools
     are invoked, the loop continues until the model stops emitting
     tool_calls. The per-call ollama timeout is the only wall-clock
     guard; a runaway loop is observable in the agent log (one
     `tool_call` line per turn) and can be killed manually.
   - POST `completed`/`failed` event to /api/log (source 'worker-mac')
   - POST result to /api/worker/result {machine_id, entry_id, status,
                                         result, error, duration_sec}

7. `views.py` `worker_result`
   - Verify the worker holds the claim (warning if mismatch — accept anyway)
   - On success: sub-entry.content = topic + "\n\n" + result
                 sub-entry.status = 'done'
   - On failure: sub-entry.content = topic + "\n\nERROR: " + error
                 sub-entry.status = 'blocked'
   - Pop in-flight worker_* fields (worker_prompt, worker_target,
     worker_staged_at, worker_claimed_at, worker_claimed_by, worker_timeout_sec)
     but PRESERVE worker_duration_sec and worker_machine_id as audit trail
   - Set is_dirty=1 so the result syncs out to clients
   - Call action_runner.research_model_complete(sub_entry)
     (failures here are caught and logged; the worker still gets a 200 —
      see Failure modes below)

8. `action_runner.py` `research_model_complete`
   In transaction.atomic():
     - Set base.gemma_status = 'done'
     - Compute dispatched models for this base
     - Check if all dispatched models are done
     - If yes: base.status = 'done', then trigger synthesis
   Synthesis dispatches Claude with the synthesis prompt and the per-model
   source links.
```

## Free-capacity reset

A worker cannot poll while it is running ollama, because `_process_work` blocks the poll loop on the inference call. Therefore **the arrival of a poll is itself a free-capacity signal** — it means the worker has nothing in flight right now. Any prior claims still attributed to that machine are by definition stale (lost work, crashed result POST, etc.).

The poll handler enforces this in `worker_poll`:
```python
stale_claims = Entry.objects.filter(
    data__worker_claimed_by=machine_id,
    deleted_at__isnull=True,
)
for stale in stale_claims:
    if stale.status not in ('active', None):
        continue
    sdata.pop('worker_claimed_by', None)
    sdata.pop('worker_claimed_at', None)
    stale.save(...)
    # Revert base.{model}_status from 'active' back to 'staged'
```

The same poll then proceeds into the long-poll loop and may immediately re-claim the entry (legitimately, this time). The net effect is that a worker that died holding a claim, came back, and polled again, will reclaim its own work without operator intervention.

**Scope**: the reset filters by `worker_claimed_by=machine_id` only — it clears **every** claim held by the polling machine regardless of which capability the poll is for. This is the right semantics for the current multi-capability worker (qwen + gemma4 + gemma4-fast on one Mac Studio) — since the worker is single-threaded on the poll loop, the arrival of *any* poll means the machine has no in-flight work on *any* capability, so any claim of any cap held by this machine is stale.

## Stale claim auto-reclaim (the 2-hour safety net)

A claim held by a worker that **never comes back** would otherwise hang the topic forever. `_claim_worker_entry` treats a claim as stale if `worker_claimed_at` is older than `WORKER_CLAIM_STALE_SECONDS = 2 * 60 * 60`. After that age, any other poll (or the same machine after a long absence) is allowed to re-claim it.

The threshold is tied to the longest legitimate *work-item* runtime, not the longest single ollama call. With the Mac-side agent loop (multi-turn tool-use runs with `lxr` and `github` MCPs), one codoc gemma work item can legitimately run ~1h; 2h gives comfortable headroom without letting a truly dead claim hang for longer than necessary. The earlier 30-minute value dated from the single-ollama-call era and was junkifying the research page's display (healthy long-running agent runs rendering as zombie/red).

## Capability whitelist

In `tjai_app/views.py`:
```python
WORKER_CAPABILITIES = {'gemma4'}
```

`worker_poll` rejects any capability name not in this set with HTTP 400. The reason: every poll writes a `worker_capability_{cap}_lastpoll` sysconfig key. Without the whitelist, a typo or stray curl test (`?capabilities=GEMMA4`, `?capabilities=nope`, `?capabilities=other`) would create permanent fake worker rows that the research page would faithfully display as "disconnected workers" forever. This actually happened on 2026-04-06 — the cleanup is documented in the troubleshooting section.

To add a new capability:
1. Add the name to `WORKER_CAPABILITIES` in `views.py`.
2. Configure a worker to advertise it in `tj` config (`worker_models` dict — map the capability name to a local ollama model tag).
3. Stage work with `data.worker_target = '<new-cap>'` from whatever dispatcher needs it.

The worker side is generic — `tj_agent/worker.py` does not know about specific capabilities; it just polls for whatever `worker_models` maps in its config (in dict insertion order — see § Capability order is priority order) and forwards each work item to `_call_ollama` with whichever ollama model is configured locally for that capability.

## Display contract: three named facts, never conflated

The research page banner displays **three named, non-overlapping facts**. This is the rule that must not be broken — the failure mode it prevents is the user reading "Agent: idle · 1 active topic · gemma:active · worker polling" as a contradiction.

### Fact 1 — Local agent (drives L1)

> `Local agent: idle (last run completed 5m ago)`
> `Local agent: running on research-foo for 12s · activity 2s ago`
> `Local agent: failed (3m ago)`

This is the *local research-agent process* — the orchestrator. It being idle while remote inference is in flight is the normal post-dispatch state, not a contradiction.

### Fact 2 — Remote workers (drives L5 + a derived state)

One line per known capability. The state is **derived**, not just `last_poll_age`:

Derived **per machine** — not per capability. Aliveness is a property of the machine; every capability the machine advertises inherits the same state. The per-cap `worker_capability_{cap}_lastpoll` rows are a denormalized view of a per-machine fact (`worker_poll` writes them in lockstep during a single request), so deriving state per-cap from them treats correlated data as independent and produces contradictions the moment one cap has a claim and the other does not.

| Derived state | Rule | Display (one line per machine) |
|---|---|---|
| `busy`   | Machine holds at least one active claim (any cap) whose age is within `WORKER_CLAIM_STALE_SECONDS` | `ed8e0e3a [gemma4, gemma4-fast] busy on <topic> (gemma4) claimed 49s` |
| `idle`   | No claim held, last poll within `WORKER_DISCONNECTED_SECONDS` (180s) | `ed8e0e3a [gemma4, gemma4-fast] idle (last poll 12s ago)` |
| `disconnected` | No claim held, no poll for longer than 180s | `ed8e0e3a [gemma4, gemma4-fast] disconnected (last poll 7m ago)` |
| `zombie` | A claim's age exceeds `WORKER_CLAIM_STALE_SECONDS` (2h) | `ed8e0e3a [gemma4] zombie claim on <topic> (gemma4), claimed 2h3m, last poll 2h3m ago — auto-reclaim within 2h` |
| `unknown` | No poll history recorded for this machine | `ed8e0e3a [gemma4] unknown` |

The key insight: workers are single-threaded on the poll loop — they can't poll while running ollama — so **any held claim is unambiguous evidence that the machine is alive and working**, regardless of how stale its poll timestamp looks. The busy/zombie split is by *claim age*, not by *poll age*. Per-cap disagreement within one machine is structurally impossible under this shape.

### Fact 3 — Topics with work in flight (drives L2 + L3)

For each base entry where any model is in `staged` or `active`:

> `• research-foo — claude done · gemini done · gemma in-progress on ed8e0e3a (49s ago)`
> `• research-bar — gemma awaiting worker (staged 2m ago)`

Per-model phases use **server-formatted ago strings** (`fmt_ago`), not client-clock arithmetic. When the worker_health for the cap is `zombie`, the topic phase renders as `gemma stale claim by aaaaaaaa (22m ago)` rather than `in-progress`, so the topic line cannot disagree with the worker line.

### Activity dot rules

| Color | Rule | Meaning |
|---|---|---|
| green pulse | `system_busy` (local running OR any worker busy) | Something is actively working |
| red | `anyZombie` OR (`anyDisconnected && stagedWaiting`) | Something is wrong |
| orange | `stagedWaiting` AND no problems | Work waiting for a free worker |
| grey | nothing in flight | Idle |

`system_busy` is computed server-side in `api_research_data` and exposed as a single boolean so the front-end never has to re-derive the rule.

## Worker configuration (the Mac side)

`tj_agent/worker.py` reads from `tj` config (`~/.tjai/config.json` on the Mac). One worker process can serve multiple capabilities, each mapped to its own ollama model:

| Key | Default | Purpose |
|---|---|---|
| `worker_enabled` | `false` | Master switch. Must be true for the worker thread to start. |
| `ollama_url` | `http://localhost:11434` | Local ollama endpoint. |
| `worker_models` | `{}` | Dict mapping each advertised capability name to its local ollama model. Each value is either a bare ollama model name (string) or `{"ollama_name": "...", "max_tokens": <int>}` for a per-capability token cap. The capability names must each be in the server's `WORKER_CAPABILITIES`. |

Example covering both today's capabilities:

```json
{
  "worker_enabled": true,
  "ollama_url": "http://localhost:11434",
  "worker_models": {
    "gemma4":      "gemma3:27b",
    "gemma4-fast": {"ollama_name": "gemma3:e4b", "max_tokens": 8000}
  }
}
```

The capability name on the server side and the local ollama model tag are decoupled — the server sends `model: gemma4` and the worker substitutes its locally configured tag. The same physical machine can serve a slow-but-thorough capability and a fast-and-light capability against the same hardware.

### Capability order is priority order

When a worker advertises multiple capabilities and more than one has staged work, **the order of `worker_models` keys in `config.json` determines which work gets claimed first.** Put the capability you want polled first at the top of the dict.

The mechanism:
- `tj_agent/worker.py` passes `capabilities = list(cfg["models"].keys())` to the poll request (Python preserves dict insertion order).
- The server's `_claim_worker_entry` in `tjai_app/views.py` iterates capabilities in request order: `for capability in capabilities:` — the first capability with matching staged work wins the claim.
- Result: config insertion order → poll order → claim order.

Worked example. Config:
```json
"worker_models": {
  "qwen":        "qwen3.6:latest",
  "gemma4":      "gemma3:27b",
  "gemma4-fast": {"ollama_name": "gemma3:e4b", "max_tokens": 8000}
}
```
With staged work for both `qwen` and `gemma4`, this worker will claim `qwen` first. When `qwen` completes and the worker polls again, `gemma4` gets claimed.

Historical note: `worker.py` previously sorted capabilities alphabetically (`sorted(cfg["models"].keys())`), which made `gemma4` always beat `qwen` regardless of config order and was surprising when one capability was consistently slow. Switched to insertion-order preservation on 2026-04-20.

This is per-worker only — it does not affect the *server's* dispatch order across multiple workers, or which model of a multi-model research topic runs first. Those are separate concerns.

`start_worker_thread()` is called from `tj_agent.daemon.run_forever()` alongside the sync loop. If `worker_enabled=false` or `worker_models={}`, the thread doesn't start. Reconfiguring requires restarting `tj_agent` on the Mac.

The legacy schema (`worker_capabilities` list + `ollama_model` + `worker_max_tokens`) is still accepted with a deprecation warning. New configs should use `worker_models`.

How `tj_agent` itself is started (login item, launchd plist, manual) is a per-machine operational detail outside this doc's scope.

The thread wraps an asyncio loop (`run_worker_forever()` calls `asyncio.run(_run_worker_forever_async())`) so the worker can drive async MCP client sessions naturally. On entry it tries to start an `McpToolDispatcher` (see § MCP tools and agent loop); if startup fails or is disabled, the worker proceeds with `dispatcher=None` and tools = `[]`. The loop:

1. Read config (re-read each iteration, so toggling `worker_enabled` takes effect on the next cycle).
2. Long-poll `/api/worker/poll` with a client timeout of `POLL_CLIENT_TIMEOUT = 70s` (must exceed the server's 50s hold).
3. If `work` is null, reconnect immediately.
4. If `work` is present, run it through `_process_work_async`:
   - POST `received` event to /api/log (source `worker-mac`) with capability, ollama_model, prompt_chars, tools_advertised, hostname.
   - Run the agent loop: send messages + tools to ollama, dispatch any tool_calls via the dispatcher, append results, repeat until ollama returns no further tool_calls. There is no turn cap. With zero tools (no dispatcher) the loop terminates after turn 1, equivalent to the old single-shot path.
   - POST `completed` (or `failed`) event to /api/log with duration, turns_used, tool_calls count, output_chars (or error).
   - POST result to /api/worker/result.
   - Errors here are caught and logged but don't crash the loop.
5. Backoff exponentially (1s → 60s) on transient poll failures (network, 5xx).

## MCP tools and agent loop (Mac side)

The Mac worker can additionally run as an **MCP client** for the model — advertising tools from one or more local MCP servers on every ollama call and dispatching the resulting `tool_calls` back to those servers in a multi-turn loop. This is **R&D**, **Mac-side only**: the server doesn't know whether the worker has tools, the wire protocol is unchanged, and the work item shape is unchanged. From the server's perspective the worker is still a black box that takes a prompt and returns a string.

The default deployment on the Mac Studio bundles two MCP servers, both spawned as stdio subprocesses by the worker on startup:

| Server | Type | Tools | Auth |
|---|---|---|---|
| `lxr-mcp-server` | Python (`mcp` SDK FastMCP) | `lxr_ident`, `lxr_search`, `lxr_source`, `lxr_list` — EIC code browser cross-references via the LXR HTTP backend at `eic-code-browser.sdcc.bnl.gov` | none (public LXR instance) |
| `github-mcp-server` | Go binary v0.32.0, `stdio` mode | 41 tools across the GitHub REST surface (`get_me`, `get_file_contents`, `search_code`, `list_pull_requests`, `add_issue_comment`, etc.) | `GITHUB_PERSONAL_ACCESS_TOKEN` env var |

Each MCP server is **optional**: if a server fails to launch (binary missing, token missing, subprocess error), the dispatcher logs the failure loudly (`WARNING ...mcp_tool_dispatcher: github-mcp-server binary not found; skipping`) and the worker keeps running with whatever subset of tools is available — **including zero**, in which case the agent loop collapses to single-shot inference, identical to the pre-MCP behavior.

### Architecture

```
                                ┌──────────────────────────────┐
                                │ tj_agent (Mac, asyncio loop) │
                                │                              │
  /api/worker/poll  ────────►  │  worker.py                   │
                                │   _process_work_async         │
                                │                              │
                                │     ┌──────────────────┐     │
                                │     │ for turn in 1..N │     │
                                │     │                  │     │       gemma4:31b
                                │     │  ollama /api/chat│─────┼────►  gemma4:e4b
                                │     │   tools=[...]    │     │       (local ollama)
                                │     │                  │     │
                                │     │  if tool_calls:  │     │
                                │     │   for each call: │     │       lxr-mcp-server
                                │     │    dispatcher    │─────┼────►  (python stdio)
                                │     │     .call_tool   │     │
                                │     │    append result │     │       github-mcp-server
                                │     │                  │     │       (go stdio)
                                │     │  else: break     │     │
                                │     └──────────────────┘     │
                                │                              │
  /api/worker/result  ◄───────  │   POST final assistant text  │
                                │                              │
  /api/log  ◄─────────────────  │   _log_to_tjai("received"    │
                                │                "completed")  │
                                │   source='worker-mac'         │
                                └──────────────────────────────┘
```

`McpToolDispatcher` (in `tj_agent/mcp_tool_dispatcher.py`) holds one long-lived `mcp.ClientSession` per spawned server, discovers each server's tools at startup via `session.list_tools()`, and maintains a `tool_owner: dict[str, str]` map for routing. The dispatcher lives for the lifetime of the worker thread; subprocesses are torn down via `AsyncExitStack.aclose()` when the thread exits (which only happens on cancellation).

### Agent loop

```
1. messages = [{role: user, content: <prompt>}]
2. while True:
       response = ollama /api/chat(messages, tools=dispatcher.tools_for_ollama())
       messages.append(response.message)         # always — model sees its prior tool_calls
       if not response.message.tool_calls:
           final_text = response.message.content
           break
       for tc in response.message.tool_calls:
           result = await dispatcher.call_tool(tc.function.name, tc.function.arguments)
           messages.append({role: tool, name: tc.function.name, content: result})
3. POST final_text to /api/worker/result
```

**There is no turn cap.** The loop terminates the moment the model stops emitting `tool_calls`. The only wall-clock guard is the per-call ollama timeout (`work.timeout_sec`, default 3600s).

Tool calls within a single turn are executed sequentially in the order ollama emitted them, results are appended in order as separate `role:tool` messages, and then the next ollama turn is sent.

The dispatcher's `call_tool` is async (`mcp.ClientSession` is async-native), and the worker drives it from inside an asyncio loop in the worker thread. The blocking ollama HTTP call is wrapped in `asyncio.to_thread()` so a long inference doesn't block the event loop.

**Tools are always advertised** when the dispatcher is up. There is no `work_type` gating — the model decides whether to use them. This is intentional R&D: we want to observe how `gemma4:31b` and `gemma4:e4b` actually behave when given tools on a research prompt vs a codoc prompt vs an agentic prompt.

### Local secrets and the wrapper script

The worker reads two secrets from environment variables on startup:

| Env var | Purpose | Required for |
|---|---|---|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Passed to `github-mcp-server` (which reads it from its own env) so it can authenticate to api.github.com | github MCP tools — server is skipped if missing |
| `TJAI_API_KEY` | Bearer token for the worker's POSTs to `/api/log`. Same value as the server's SysConfig `gmail_addon_api_key`. | Per-prompt central logging — silently no-ops if missing |

These are loaded from `~/.tjai/env` (a chmod-600 file outside the repo, outside Dropbox) by `~/.tjai/run_agent.sh` (a wrapper script that sources the env file and execs the venv python). The launchd plist `~/Library/LaunchAgents/com.tj_agent.plist` invokes the wrapper rather than python directly. None of these three files (env, wrapper, plist) is in the repo — they are per-machine local state.

Two paths are also configurable via env, with sensible defaults so most setups need not set them:

| Env var | Default | Purpose |
|---|---|---|
| `LXR_MCP_SERVER_PATH` | `~/github/lxr-mcp-server/lxr_mcp_server.py` | Where the lxr server lives |
| `GITHUB_MCP_SERVER_BIN` | `shutil.which('github-mcp-server')` then `~/bin/github-mcp-server` | Where the github server binary lives |

To rotate any of these values, edit `~/.tjai/env` and restart `tj_agent` via:

```bash
~/github/tjrepo/tjai/scripts/kill_worker.sh env rotation
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tj_agent.plist
```

`scripts/kill_worker.sh` is the deterministic kill — it POSTs `status='failed'` for any in-flight claim before running `launchctl bootout`, so the server doesn't see a stale 2h zombie. See § Stopping a worker cleanly. If no work is in flight the abort step is a safe no-op, so the script is fine to run unconditionally.

`launchctl kickstart -k gui/$(id -u)/com.tjai.agent` is **not** sufficient — it only restarts the running job, it does not re-read the plist or re-source the env file. New env vars added to `~/.tjai/env` will not be visible to the worker until the bootout/bootstrap pair is run.

The bootout/bootstrap pair is also the correct way to pick up **code changes** to `tj_agent/*.py`. `tj config.json` is re-read on every poll iteration (so toggling `worker_enabled` or reordering `worker_models` is picked up on the next poll without any restart), but edits to the worker Python itself — e.g., changing the poll-capability-order logic in `worker.py` — only take effect on next process start.

### Per-prompt logging to `/api/log`

The worker posts events to tjai's central `AppLog` via `POST /api/log` (Bearer-authenticated with `TJAI_API_KEY`), source `worker-mac`. Three event types per work item:

| Event | Level | Message format | extra_data fields |
|---|---|---|---|
| received | info | `received <work_type> prompt <entry_id> via <cap>=<ollama_model> (N chars, M tools available) on <hostname>` | `event, entry_id, machine_id, hostname, capability, ollama_model, work_type, prompt_chars, tools_advertised` |
| completed | info | `completed <entry_id> in Ns (N turn(s), N tool call(s), N output chars) via <cap>=<ollama_model>` | `event, entry_id, machine_id, hostname, capability, ollama_model, duration_sec, turns_used, tool_calls, output_chars` |
| failed | error | `failed <entry_id> after Ns (turn N/12, M tool call(s)): <error>` | `event, entry_id, machine_id, hostname, capability, ollama_model, duration_sec, turns_used, tool_calls, error` |

These complement the existing server-side `worker_poll` and `worker_result` log lines (source `worker`) which fire on dispatch and result-receipt. The `worker-mac` lines surface the **worker's** view and include details only the worker knows: actual ollama model used (vs the capability name), prompt char count, tools advertised, turns used in the agent loop, hostname, and the per-loop tool-call total.

Logging failures never disrupt work processing — `_log_to_tjai` swallows all exceptions and continues.

### Worker dependencies

Beyond the stdlib-only base sync daemon, the optional remote worker pulls in three Python packages, listed in `tj_agent/requirements.txt`:

```
mcp>=1.27.0          # client SDK for MCP servers
httpx>=0.28          # used by lxr-mcp-server (transitive)
beautifulsoup4>=4.14 # used by lxr-mcp-server (transitive)
```

Install them into the same venv that runs `tj_agent`:

```bash
~/.tjai/venv/bin/python3 -m pip install -r tj_agent/requirements.txt
```

The `mcp` package is **lazy-imported** inside `_run_worker_forever_async`, not at module top, so `tj_agent.worker` and `tj_agent.daemon` import cleanly on machines that don't have it installed (every machine with `worker_enabled=false`, which is every machine except the Mac Studio today).

## Failure modes

Things that can go wrong and what the system does about them.

| Failure | What happens | Recovery |
|---|---|---|
| **A model finishes `failed`** | `research_model_complete` waits — synthesis requires every dispatched model to be `done`. The base entry stays active with the failed model visible in the banner. This is a deliberate human-in-the-loop checkpoint. | Investigate the failure (worker log, prompt size, ollama state, etc.), then click **Rerun selections** on the research page with the failed model checked. The rerun re-dispatches through `_dispatch_research_3way`; on success the all-done check passes and synthesis fires automatically. |
| **`research_model_complete` raises** | `worker_result` catches the exception, logs it, but still returns `200` to the worker. The base entry's status is silently broken. | Read the gunicorn log for `worker_result: research_model_complete failed:`. No automated recovery. |
| **Worker POSTs result for a claim it doesn't hold** | A warning is logged; the result is accepted anyway (the work was done — refusing it would just throw away real output). | None needed in normal operation. Two workers fighting over the same claim shouldn't happen in production. |
| **Worker dies mid-inference (graceful, via `scripts/kill_worker.sh`)** | The script POSTs `status='failed'` for the current claim *before* bootout. Server transitions sub-entry to `failed` immediately; UI stops showing it as running within one refresh. | Intentional — this is the clean path. See § Stopping a worker cleanly. |
| **Worker dies mid-inference (ungraceful, e.g. SIGKILL, crash)** | Local `~/.tjai/current_claim.json` marker is left behind. On next `tj_agent` startup, `_run_worker_forever_async` calls `abort.abort_current_claim()` which POSTs `failed` and clears the marker before entering the poll loop. | Automatic on next startup. If tj_agent is not restarted, run `python -m tj_agent abort` manually. |
| **Worker process gone permanently** | If a startup never happens and no operator runs `tj_agent abort`, the claim sits until `WORKER_CLAIM_STALE_SECONDS` (2h), then any poll can re-claim it. | Eliminate the delay with `python -m tj_agent abort` on the Mac, OR let the 2-hour auto-reclaim fire. The display calls this `zombie` once the claim age crosses the threshold, otherwise just `busy`. |
| **Server restarts mid-claim** | The worker's HTTP request gets a connection-reset; its retry loop backs off and eventually polls again, hitting the free-capacity reset path. | Automatic. |
| **Result POST fails (network)** | Worker logs the failure; the work is lost; the claim remains until the worker's next poll triggers the free-capacity reset, which re-stages the work for re-claim. | Automatic. The work runs again. |
| **Worker advertises an unknown capability** | `400` from `worker_poll`; sysconfig is not polluted. Worker logs the rejection and backs off. | Edit `WORKER_CAPABILITIES` on the server, or fix the worker config. |
| **MCP server fails to launch** (binary missing, token missing, subprocess error) | The dispatcher logs `WARNING ...mcp_tool_dispatcher: <server> ...; skipping`. Worker keeps running with whatever subset of MCP servers came up. If all fail, the worker proceeds with `tools=[]` — agent loop collapses to single-shot inference. No work is rejected. | Fix the server-specific cause (install binary, set env var, etc.) and bootout/bootstrap the launchd job. |
| **MCP `call_tool` raises during the agent loop** | The exception is caught per-tool, the error text is fed back to the model as the tool result (`TOOL ERROR: <ExceptionType>: <message>`). The model can choose to retry, try a different tool, or give up and answer in text. The work item completes normally. | None — this is by design. The model gets to see and react to tool failures. |
| **Worker posts to `/api/log` and the call fails** | Logged locally as a warning; work processing continues unaffected. No retry. | None — central logging is best-effort by design; the local agent.log line is the source of truth. |
| **`TJAI_API_KEY` not set in `~/.tjai/env`** | `_post_log_sync` silently no-ops on every event. No central logging happens. The worker still works normally. | Add the key to `~/.tjai/env` and bootout/bootstrap. |

## Stopping a worker cleanly

The remote-worker protocol has no "cancel" message. The only way for the server to learn that in-flight work won't complete is a `POST /api/worker/result` with `status='failed'`. If a worker is killed (SIGTERM from `launchctl bootout`, SIGKILL, crash, power loss) without posting that failure, the server keeps showing the claim as busy until `WORKER_CLAIM_STALE_SECONDS` (2h) elapses — a long, avoidable lag between reality and the UI.

**Don't run bare `launchctl bootout`** when a worker is processing work. Use:

```bash
~/github/tjrepo/tjai/scripts/kill_worker.sh [optional reason text]
```

This is the deterministic, committed kill. It does two things in order:

1. `python -m tj_agent abort "<reason>"` — reads `~/.tjai/current_claim.json` (the in-flight marker the worker maintains, see below), POSTs `status='failed'` for that `entry_id` with the reason, and removes the marker. Works whether `tj_agent` is running or already dead — it only needs the local marker and the server API.
2. `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tj_agent.plist` — stops the process.

The server transitions the sub-entry from `active` to `failed` immediately; the UI stops showing it as running within one refresh cycle.

### The `current_claim.json` marker

`tj_agent/worker.py` writes `~/.tjai/current_claim.json` at the start of every work item in `_process_work_async` and removes it on every completion path (success, inference error, empty-final-text failure). Shape:

```json
{
  "entry_id": "<sub-entry uuid>",
  "capability": "gemma4",
  "base_entry_id": "research-<topic>",
  "claimed_at": 1776715123.4,
  "ollama_model": "gemma4:31b"
}
```

The marker is the local record of "this worker currently owes the server a result for this entry." It is the contract that makes `abort` safe to invoke without coordination with a running worker — the marker exists iff there's a claim to abort.

### Startup crash recovery

If a worker dies ungracefully (SIGKILL, OOM, power loss) the marker is left behind. On the next `tj_agent` startup, `_run_worker_forever_async` calls `abort.abort_current_claim()` before entering the poll loop. This POSTs `status='failed'` for the stranded entry and clears the marker, so the first poll doesn't race with a stale `active` claim. A graceful shutdown whose POST hit a network error is also recovered this way on next start.

### Why not a SIGTERM handler in the worker thread

Python signal handlers run in the main thread, but the worker is a separate thread running an asyncio loop. Delivering a clean cancel from a signal handler into that thread (while it's blocked inside `asyncio.to_thread` on a long ollama call) is fragile — the ollama HTTP request would need to be torn down, the message history discarded, and a POST made before the parent process exits in the time the launchd gives us between SIGTERM and SIGKILL (typically seconds).

The current design sidesteps that: `scripts/kill_worker.sh` does the POST **before** stopping the process, using the local marker. The marker-based recovery handles the residual "got killed without scripts/kill_worker.sh" case on next startup. Simple, correct, no signal-thread-asyncio interaction surface.

### When to use which

| Situation | Use |
|---|---|
| Operator killing the worker for any reason (restage, debug, shutdown) | `scripts/kill_worker.sh` |
| Just POSTing failure without stopping the worker (e.g., because prompt needs refresh) | `python -m tj_agent abort "<reason>"`, then let the worker keep running for the next work item |
| tj_agent already crashed, need to clean server state | `python -m tj_agent abort "post-crash cleanup"` — reads the leftover marker and POSTs |
| Updating env vars or re-sourcing `~/.tjai/env` | `scripts/kill_worker.sh` then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tj_agent.plist` |

## Troubleshooting

### Poll keeps returning HTTP 500

Check the gunicorn error log:
```bash
journalctl -u tjai-gunicorn -n 200 --no-pager | grep -B 2 -A 60 "Internal Server Error: /tjai/api/worker/poll"
```

The April 2026 incident was an `AppLog.objects.create(timestamp=time.time(), level='INFO', ...)` call in `worker_poll` and `worker_result` — `AppLog.timestamp` is a `DateTimeField` and `AppLog.level` is an `IntegerField`. Django/Python 3.14 rejects float-to-datetime coercion. The fix pattern (used everywhere else in the codebase) is:

```python
from django.utils import timezone as tz
AppLog.objects.create(
    source='worker',
    timestamp=tz.now(),
    level=logging.INFO,
    levelname='INFO',
    message=...,
)
```

Any new endpoint that writes AppLog must follow this pattern. Pass datetimes to DateTimeField, ints to IntegerField.

### "gemma awaiting worker (staged X ago)" but nothing happens

Check on the Mac whether `tj_agent` is running and the worker thread started:
```bash
pgrep -af tj_agent     # process alive?
tail -F ~/.tjai/agent.log | grep -i worker
```

Confirm the worker is configured:
```bash
jq '.worker_enabled, .worker_models' ~/.tjai/config.json
```

If the worker is polling but the server returns 500, see the previous section.

### Bogus "disconnected workers" appear in the banner

Symptom: capability names like `nope`, `GEMMA4`, `other` show up as disconnected workers in the Remote workers line. Cause: someone (a curl test, a typo, a misconfigured worker) called `worker_poll` with a capability name that the server happily recorded into sysconfig.

The capability whitelist now rejects unknown names at the endpoint, but pre-existing pollution must be cleaned up by hand. Use a Django shell:

```bash
set -a && source /var/www/tjai/.env && set +a
cd /home/admin/github/tjrepo/tjai
.venv/bin/python manage.py shell <<'PY'
from tjai_app.models import SysConfig
KEEP = {'gemma4'}  # legitimate capability names
for sc in SysConfig.objects.filter(
    key__startswith='worker_capability_', key__endswith='_lastpoll'
):
    cap = sc.key[len('worker_capability_'):-len('_lastpoll')]
    if cap not in KEEP:
        print(f"deleting {sc.key}")
        sc.delete()
PY
```

### Stuck claim from a dead worker

A claim held by a worker that never returned will auto-clear after `WORKER_CLAIM_STALE_SECONDS = 2h` — the next poll for that capability re-claims the work. Until then, the banner will show it as `zombie` (red dot, "auto-reclaim within 2h").

To clear a stuck claim immediately:
```bash
.venv/bin/python manage.py shell <<'PY'
from tjai_app.models import Entry
import time
sub = Entry.objects.filter(
    data__entry_id='research-foo-gemma',
    deleted_at__isnull=True,
).first()
sd = sub.data
sd.pop('worker_claimed_by', None)
sd.pop('worker_claimed_at', None)
sub.data = sd
sub.timestamp_modified = time.time()
sub.save(update_fields=['data', 'timestamp_modified'])

base = Entry.objects.filter(data__entry_id='research-foo').first()
bd = base.data
if bd.get('gemma_status') == 'active':
    bd['gemma_status'] = 'staged'
    base.data = bd
    base.timestamp_modified = time.time()
    base.save(update_fields=['data', 'timestamp_modified'])
PY
```

### Inspecting live state from the server

```bash
set -a && source /var/www/tjai/.env && set +a
cd /home/admin/github/tjrepo/tjai
.venv/bin/python manage.py shell <<'PY'
from tjai_app.models import Entry, SysConfig
import json, time
now = time.time()

print("Worker capability heartbeats:")
for sc in SysConfig.objects.filter(
    key__startswith='worker_capability_', key__endswith='_lastpoll'
).order_by('key'):
    info = json.loads(sc.value) if sc.value else {}
    age = int(now - float(info.get('ts'))) if info.get('ts') else None
    print(f"  {sc.key}: machine={info.get('machine_id','')[:8]} age={age}s")

print()
print("Sub-entries with worker_target:")
for sub in Entry.objects.filter(
    data__source='multimodel', data__worker_target__isnull=False,
    deleted_at__isnull=True,
):
    sd = sub.data
    cl_at = sd.get('worker_claimed_at')
    print(f"  {sd.get('entry_id')} status={sub.status}")
    print(f"    worker_target={sd.get('worker_target')}")
    print(f"    worker_claimed_by={(sd.get('worker_claimed_by') or '')[:8]}")
    print(f"    claimed_age={int(now - float(cl_at)) if cl_at else None}s")
PY
```

### Reproducing a poll by hand

```bash
curl -s -o /tmp/r.json -w 'HTTP %{http_code} %{time_total}s\n' \
  --max-time 70 \
  'http://127.0.0.1:8002/tjai/api/worker/poll?machine_id=test-machine&capabilities=gemma4'
cat /tmp/r.json
```

Note: doing this with the **real** Mac's `machine_id` will steal the claim from the real worker. The Mac's next poll will free-capacity-reset the claim and re-claim cleanly, but the test will briefly show two workers fighting over the same entry. Use a different `machine_id` for safe testing.

## Adding a second remote-worker capability

The pipeline is generic — adding a second model is straightforward:

1. **Server: register the capability.** Add the new name to `WORKER_CAPABILITIES` in `tjai_app/views.py`.
2. **Server: stage work for it.** A dispatcher writes an `Entry` with `data.worker_target='<cap>'`, `data.worker_prompt='<text>'`, `data.worker_staged_at=time.time()`, optionally `data.worker_timeout_sec=<int>`, and `Entry.status='active'`. For research-style integration, also write the per-model status fields (`{model}_status='staged'`, `{model}_entry_id=<id>`) on the base entry so `research_model_complete` will know to expect this model's contribution.
3. **Worker: advertise it.** On the worker machine, add an entry in `worker_models` mapping the new capability name to the appropriate local ollama model tag. Put it at the position in the dict that reflects its priority relative to other capabilities (§ Capability order is priority order). Restart `tj_agent` via `launchctl bootout`/`bootstrap`.
4. **Result handling.** If the work is for the multi-model research pipeline, no further work is needed — `worker_result` calls `research_model_complete`, which is generic over `RESEARCH_MODELS`. If the work is for something else, extend `worker_result` to dispatch on `data.source` and route accordingly.

The display layer is automatic: `api_research_data` synthesizes `worker_health[<cap>]` for any cap that has either a poll record or a held claim, and the banner renders one line per cap.

## Cross-references

- `docs/agents.md` — Research Queue (the higher-level Claude+Gemini+Gemma pipeline this plugs into)
- `docs/action-agent.md` — Action Agent execution model that the local research-agent runs under
- `docs/architecture.md` — Multi-device sync architecture; remote workers are a parallel mechanism that does not flow through the sync daemon
