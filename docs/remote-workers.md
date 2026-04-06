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
| Dispatcher | `tjai_app/action_runner.py` `_dispatch_research_3way` | When a research topic is submitted, creates per-model sub-entries and stages gemma work with `worker_target='gemma4'` |
| Endpoint (poll) | `tjai_app/views.py` `worker_poll`, `_claim_worker_entry` | Long-polls for matching work, atomically claims under transaction, returns prompt |
| Endpoint (result) | `tjai_app/views.py` `worker_result` | Receives result, finalizes the sub-entry, calls `research_model_complete` |
| Per-completion hook | `tjai_app/action_runner.py` `research_model_complete` | Updates base entry's `{model}_status`, triggers synthesis once all dispatched models are done |
| Worker (Mac) | `tj_agent/worker.py` | Loop that polls, calls local ollama, POSTs result |
| Worker thread starter | `tj_agent/daemon.py` `run_forever` | Spawns the worker thread alongside the sync loop on `tj_agent` startup |
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
  "work_type":     "research" | "generic",
  "model":         "<capability name, e.g. gemma4>",
  "prompt":        "<full prompt text>",
  "timeout_sec":   <int>,
  "base_entry_id": "<base research entry_id, or null>"
}}

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

### Constants

| Symbol | Value | Defined in | Meaning |
|---|---|---|---|
| `WORKER_POLL_HOLD_SECONDS` | 50 | `tjai_app/views.py` | Server holds the long-poll for up to this long. Must stay under gunicorn's `--timeout` (120s). |
| `WORKER_POLL_INTERVAL` | 2 | `tjai_app/views.py` | DB recheck frequency inside the hold loop. |
| `WORKER_CLAIM_STALE_SECONDS` | 1800 | `tjai_app/views.py` | A claim older than this can be auto-reclaimed by any poll. |
| `WORKER_CAPABILITIES` | `{'gemma4'}` | `tjai_app/views.py` | Capability whitelist. Edit this to add a new capability. |
| `POLL_CLIENT_TIMEOUT` | 70 | `tj_agent/worker.py` | Worker socket timeout. Must exceed `WORKER_POLL_HOLD_SECONDS` (the 20s margin covers round-trip + safety). |
| `POLL_BACKOFF_INITIAL` / `MAX` | 1.0 / 60.0 | `tj_agent/worker.py` | Exponential backoff between failed polls. |
| `DEFAULT_INFERENCE_TIMEOUT` | 1800 | `tj_agent/worker.py` | Worker's local timeout on the ollama call, overridden by `work.timeout_sec`. |

The "no poll for longer than 180s" disconnected threshold is a function-local value inside `api_research_data`, not a top-level constant.

## State surfaces

There are five independent pieces of state. They have orthogonal lifecycles and **must never be conflated in display text** — that is the failure mode that produced the "Agent: idle · 1 active topic · gemma in-progress · worker polling" contradictions of 2026-04-06.

| # | Where it lives | Purpose | Values |
|---|---|---|---|
| L1 | `SysConfig['agent_research-agent_status']` | The **local research-agent process** — the orchestrator that builds prompts, dispatches Claude/Gemini directly, stages gemma, and writes synthesis. | `idle`, `running`, `failed` |
| L2 | base entry's `Entry.status` | Whether the **topic as a whole** is finalized | `pending`, `active`, `done`, `blocked` |
| L3 | base entry's `data.{model}_status` for each model in `RESEARCH_MODELS = ('claude','gemini','gemma')` | Per-model lifecycle for this topic | `None`, `staged`, `active`, `done`, `blocked`, `rerun` |
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
| `active` | `blocked` | Worker POSTs `status='failed'` | `worker_result` |
| `done` / `blocked` | `rerun` | User clicks "Rerun selections" with this model checked | `api_research_rerun_models` |
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
           is older than WORKER_CLAIM_STALE_SECONDS = 30 min)
         * Set worker_claimed_by = machine_id           ← L4
                worker_claimed_at = now
         * Save the sub-entry
         * If base_entry_id and model are set:
             upgrade base.{model}_status from 'staged' to 'active'  ← L3
         * return the locked entry
   (Whole block atomic — if base update fails, claim rolls back.)

5. worker_poll returns the work payload (see Wire protocol § GET
   /api/worker/poll for the exact shape) and writes an AppLog row.

6. Mac worker (`tj_agent/worker.py` `_process_work`)
   - POST to local ollama /api/chat with the prompt and configured model
   - Block until ollama returns (or timeout / error)
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

**Scope**: the reset filters by `worker_claimed_by=machine_id` only — it clears **every** claim held by the polling machine regardless of which capability the poll is for. This is correct under the current "one worker, one capability per process" assumption (a polling worker has free capacity full stop, not just for the polled cap). If a single worker ever advertises multiple capabilities, this is still likely the right semantics, but it's a place to recheck.

## Stale claim auto-reclaim (the 30-minute safety net)

A claim held by a worker that **never comes back** would otherwise hang the topic forever. `_claim_worker_entry` treats a claim as stale if `worker_claimed_at` is older than `WORKER_CLAIM_STALE_SECONDS = 30 * 60`. After that age, any other poll (or the same machine after a long absence) is allowed to re-claim it.

30 minutes was chosen to exceed the maximum reasonable inference time (`DEFAULT_INFERENCE_TIMEOUT = 1800` in `tj_agent/worker.py`). Long inferences that legitimately take 10–25 minutes are not disturbed.

## Capability whitelist

In `tjai_app/views.py`:
```python
WORKER_CAPABILITIES = {'gemma4'}
```

`worker_poll` rejects any capability name not in this set with HTTP 400. The reason: every poll writes a `worker_capability_{cap}_lastpoll` sysconfig key. Without the whitelist, a typo or stray curl test (`?capabilities=GEMMA4`, `?capabilities=nope`, `?capabilities=other`) would create permanent fake worker rows that the research page would faithfully display as "disconnected workers" forever. This actually happened on 2026-04-06 — the cleanup is documented in the troubleshooting section.

To add a new capability:
1. Add the name to `WORKER_CAPABILITIES` in `views.py`.
2. Configure a worker to advertise it in `tj` config (`worker_capabilities` list).
3. Stage work with `data.worker_target = '<new-cap>'` from whatever dispatcher needs it.

The worker side is generic — `tj_agent/worker.py` does not know about specific capabilities; it just polls for whatever `worker_capabilities` is set to in its config and forwards each work item to `_call_ollama` with whichever ollama model is configured locally.

## Display contract: three named facts, never conflated

The research page banner displays **three named, non-overlapping facts**. This is the rule that must not be broken — the failure mode it prevents is the user reading "Agent: idle · 1 active topic · gemma:active · worker polling" as a contradiction.

### Fact 1 — Local agent (drives L1)

> `Local agent: idle (last run completed 5m ago)`
> `Local agent: running on research-foo for 12s · activity 2s ago`
> `Local agent: failed (3m ago)`

This is the *local research-agent process* — the orchestrator. It being idle while remote inference is in flight is the normal post-dispatch state, not a contradiction.

### Fact 2 — Remote workers (drives L5 + a derived state)

One line per known capability. The state is **derived**, not just `last_poll_age`:

| Derived state | Rule | Display |
|---|---|---|
| `busy`   | A sub-entry is claimed by the same machine_id that last polled this cap | `gemma4: busy on <topic> (claimed 49s ago) ed8e0e3a` |
| `idle`   | Last poll within `WORKER_DISCONNECTED_SECONDS` (180s), no claim held | `gemma4: idle (last poll 12s ago) ed8e0e3a` |
| `disconnected` | No poll for longer than 180s, no claim held | `gemma4: disconnected (last poll 7m ago) ed8e0e3a` |
| `zombie` | A claim is held by a machine **other than** the last poller | `gemma4: zombie claim on <topic> (claimed 22m ago) — auto-reclaim within 30m` |
| `unknown` | No poll history at all | `gemma4: never polled` |

The key insight: workers can't poll while running ollama (`_process_work` blocks the poll loop), so **a held claim by the polling machine is unambiguous evidence of inference in progress**. This disambiguates "long inference" from "disconnected" — both have stale poll timestamps, only one has a held claim.

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

`tj_agent/worker.py` reads from `tj` config (`~/.tjai/config.json` on the Mac):

| Key | Default | Purpose |
|---|---|---|
| `worker_enabled` | `false` | Master switch. Must be true for the worker thread to start. |
| `worker_capabilities` | `[]` | List of capability names to advertise. Must be a subset of the server's `WORKER_CAPABILITIES`. |
| `ollama_url` | `http://localhost:11434` | Local ollama endpoint. |
| `ollama_model` | `gemma4:e4b` | The actual ollama model tag passed to `/api/chat`. The capability name on the server side is decoupled — the server sends `model: gemma4` and the worker substitutes its locally configured tag. |
| `worker_max_tokens` | unset | If set, passed to ollama as `options.num_predict`. |

`start_worker_thread()` is called from `tj_agent.daemon.run_forever()` alongside the sync loop. If `worker_enabled=false` or `worker_capabilities=[]`, the thread doesn't start. Reconfiguring requires restarting `tj_agent` on the Mac.

How `tj_agent` itself is started (login item, launchd plist, manual) is a per-machine operational detail outside this doc's scope.

The thread loops forever:
1. Read config (re-read each iteration, so toggling `worker_enabled` takes effect on the next cycle).
2. Long-poll `/api/worker/poll` with a client timeout of `POLL_CLIENT_TIMEOUT = 70s` (must exceed the server's 50s hold).
3. If `work` is null, reconnect immediately.
4. If `work` is present, run it through `_process_work`:
   - POST the prompt to ollama.
   - On success or failure, POST a result back.
   - Errors here are caught and logged but don't crash the loop.
5. Backoff exponentially (1s → 60s) on transient poll failures (network, 5xx).

## Failure modes

Things that can go wrong and what the system does about them.

| Failure | What happens | Recovery |
|---|---|---|
| **A model finishes `blocked`** | `research_model_complete` waits — synthesis requires every dispatched model to be `done`. The base entry stays active with the failed model visible in the banner. This is a deliberate human-in-the-loop checkpoint. | Investigate the failure (worker log, prompt size, ollama state, etc.), then click **Rerun selections** on the research page with the failed model checked. The rerun re-dispatches through `_dispatch_research_3way`; on success the all-done check passes and synthesis fires automatically. |
| **`research_model_complete` raises** | `worker_result` catches the exception, logs it, but still returns `200` to the worker. The base entry's status is silently broken. | Read the gunicorn log for `worker_result: research_model_complete failed:`. No automated recovery. |
| **Worker POSTs result for a claim it doesn't hold** | A warning is logged; the result is accepted anyway (the work was done — refusing it would just throw away real output). | None needed in normal operation. Two workers fighting over the same claim shouldn't happen in production. |
| **Worker dies mid-inference** | Claim stays held. The next time *that same machine* polls, the free-capacity reset clears the claim and the same poll re-claims (or another worker can claim once `WORKER_CLAIM_STALE_SECONDS` elapses). | Automatic. |
| **Worker process gone permanently** | Claim stays held until `WORKER_CLAIM_STALE_SECONDS` (30 min), then any poll can re-claim it. | Automatic, with a 30-minute worst-case delay. The display calls this `zombie` if a different worker is polling, otherwise just `busy`. |
| **Server restarts mid-claim** | The worker's HTTP request gets a connection-reset; its retry loop backs off and eventually polls again, hitting the free-capacity reset path. | Automatic. |
| **Result POST fails (network)** | Worker logs the failure; the work is lost; the claim remains until the worker's next poll triggers the free-capacity reset, which re-stages the work for re-claim. | Automatic. The work runs again. |
| **Worker advertises an unknown capability** | `400` from `worker_poll`; sysconfig is not polluted. Worker logs the rejection and backs off. | Edit `WORKER_CAPABILITIES` on the server, or fix the worker config. |

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
tail -F ~/.tjai/logs/agent.log | grep -i worker
```

Confirm the worker is configured:
```bash
jq '.worker_enabled, .worker_capabilities' ~/.tjai/config.json
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

A claim held by a worker that never returned will auto-clear after `WORKER_CLAIM_STALE_SECONDS = 30 min` — the next poll for that capability re-claims the work. Until then, the banner will show it as `zombie` (red dot, "auto-reclaim within 30m").

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
3. **Worker: advertise it.** On the worker machine, add the new capability name to `worker_capabilities` in `tj` config and configure `ollama_model` to the appropriate ollama tag. Restart `tj_agent`.
4. **Result handling.** If the work is for the multi-model research pipeline, no further work is needed — `worker_result` calls `research_model_complete`, which is generic over `RESEARCH_MODELS`. If the work is for something else, extend `worker_result` to dispatch on `data.source` and route accordingly.

The display layer is automatic: `api_research_data` synthesizes `worker_health[<cap>]` for any cap that has either a poll record or a held claim, and the banner renders one line per cap.

## Cross-references

- `docs/agents.md` — Research Queue (the higher-level Claude+Gemini+Gemma pipeline this plugs into)
- `docs/action-agent.md` — Action Agent execution model that the local research-agent runs under
- `docs/architecture.md` — Multi-device sync architecture; remote workers are a parallel mechanism that does not flow through the sync daemon
