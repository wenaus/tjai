# SysConfig

`sysconfig` is the server's key/value table for settings and runtime state:
settings and thresholds, collector cursors, process heartbeats, request flags,
job status, page preferences, source registries and two credentials. It lives
in PostgreSQL only. Clients read it through the server API; the per-machine
SQLite cache holds no copy.

The System page, https://etaverse.com/tjai/system/, lists every key with its
value and age.

## Table

Model `SysConfig` in `tjai_app/models.py`, created by migration
`0002_sysconfig`.

| Column | Type | Meaning |
|--------|------|---------|
| `key` | varchar(255), primary key | The name |
| `value` | text | The value, always stored as text |
| `description` | text, optional | Free description; rarely set |
| `timestamp_modified` | float | Unix seconds of the last write, set by each writer |

A write replaces the value. Sysconfig has no version history; entry versions
([versions.md](versions.md)) do not cover it.

## Values

Every value is text, and each reader converts it:

- **Times** used by heartbeats and lifecycle stamps are unix seconds
  (`action_agent_heartbeat`, `agent_<action_id>_launched`). Cursors that track
  an external feed hold ISO 8601 UTC (`capcom_github_prs_cursor`).
- **Request flags** (`action_agent_wake_requested`,
  `action_agent_restart_requested`, `system_health_refresh_requested`,
  `capcom_force_run`) hold a request as a non-empty value. The action agent
  polls them every 3 seconds and empties each key when it takes the request:
  `system_health_refresh_requested` is emptied before the refresh runs, and
  `capcom_force_run` (`1` or `*` for every source, otherwise one source name)
  is emptied by a conditional update, so a request written in the meantime is
  kept for the next poll.
- **Structured values** are JSON text, parsed with `json.loads` by each reader:
  registries (`capcom_sources`), page state (`capcom_state`, `capcom_pin_order`)
  and result snapshots (`system_health_data`, `watchdog_last_results`).
  `system_health_data`, the full health snapshot, is the largest value.

## Key families

Keys are named `<component>_<name>`. The System page shows the complete live
list.

| Keys | Holds | Documented in |
|------|-------|---------------|
| `agent_<action_id>_<field>` | Per-action lifecycle: `status`, `launched`, `completed`, `last_activity`, `health`, `process_alive`, `tracking`, `last_error`, `last_error_time`; also `agent_runaway_blocked_<action_id>` and `agent_kill_requested`. Most rows belong to this family. | [action-agent.md](action-agent.md), [agents.md](agents.md) |
| `action_agent_*` | Action-agent daemon PID, heartbeat, start time, wake and restart flags | [action-agent.md](action-agent.md) |
| `capcom_*` | Source registry, state tiles and their order, pin order, last pad, collector cursors, failure counters, manual run request | [capcom.md](capcom.md) |
| `watchdog_*` | Last run, last results, status, entry-flood state | [action-agent.md](action-agent.md) |
| `system_health_*` | Health snapshot, status, LLM alarms, refresh flag | [server.md](server.md) |
| `assessment_*` | Per-call token cap; rerun and backfill flags | [assessment.md](assessment.md) |
| `wrangler_*` | Wrangler daemon heartbeat, PID, start time, work in flight | [wrangler.md](wrangler.md) |
| `worker_capability_<cap>_lastpoll` | Last poll per remote-worker capability, `{machine_id, ts}` | [remote-workers.md](remote-workers.md) |
| `tg_*` | Telegram bot heartbeat and voice mode | [telegram.md](telegram.md) |
| `timezone`, `sync_interval_seconds`, `dashboard_named_sort`, `daily_history_rerun_date` | Single settings and requests | [dashboard.md](dashboard.md), [configuration.md](configuration.md), [agents.md](agents.md) |
| `gmail_addon_api_key`, `mcp_bearer_token` | The REST bearer and the MCP service bearer | [addons.md](addons.md), [mcp.md](mcp.md) |

## Access

- **Server code** reads and writes the model directly:
  `SysConfig.objects.filter(key=...).first()` to read, and
  `SysConfig.objects.update_or_create(key=..., defaults={'value': ...,
  'timestamp_modified': time.time()})` to write. There is no shared accessor
  module; each caller sets `timestamp_modified` and converts its own values.
- **`POST /tjai/api/command`** (`api_command` in `tjai_app/views.py`) serves
  pages and the CLI, authenticated by the Django session or the REST bearer. Its
  commands:
  - `set_sysconfig` (`key`, `value`) stores `str(value)`.
  - `get_sysconfig` returns every value except the two credentials.
  - `increment_json` (`key`, `field`) adds one to a field of a JSON value,
    starting from `{}` when the key is missing or not valid JSON.

  Writes to `gmail_addon_api_key` and `mcp_bearer_token` are refused with 403.
- **The `tj` CLI** goes through `api/command`. Its status display reads
  heartbeats (`tj/cli.py`, `tj/state.py`); `tj restart-agent` sets
  `action_agent_restart_requested`; `tj admin agent interval` sets
  `sync_interval_seconds`.
- **The sync agent** receives `sync_interval_seconds`, and no other key, with
  each pull and adopts it as its interval (`tj_agent/daemon.py`).
- **The MCP service** reads its bearer from `mcp_bearer_token` on each
  request (`tjai_project/mcp_asgi.py`).
- **Data migrations** seed and extend registry keys; migrations 0021, 0022 and
  0024 edit `capcom_sources`, and 0022 also `capcom_state` and
  `capcom_state_order`.
- **The System page** (`api_system_data`) lists all keys in key order. It
  masks the value of any key whose name contains `key`, `secret`, `token` or
  `password`, shortens other values to 200 characters, and shows a value that
  is a unix time in the current era as an age.

## Behaviour

- `increment_json` reads the value, adds one and saves it without a row lock.
  Two concurrent increments can read the same count, so one increment is lost
  and both callers receive the same number. It suits counters where a lost
  increment is harmless; it cannot issue unique numbers.
- `get_sysconfig` returns all non-credential values in one response, the
  health snapshot included, however few keys the caller needs.
- Masking on the System page depends on the key's name. A credential stored
  under a name without one of the four words is displayed.
- A row outlives the code that wrote it. A key no longer read by any code
  stays in the table, and on the System page, until it is deleted.
