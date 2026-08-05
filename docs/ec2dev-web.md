# ec2dev-web traffic monitor

`ec2dev-web` is the TJAI-owned monitor for the public web services entering
ec2dev. It observes traffic at Caddy, relates it to backend load, records
durable aggregate history, feeds the Snapper `ec2dev-web` scope, and owns the
`ec2dev-web` Capcom state.

It is a deterministic collector: a cron-run script with no AI involvement and
no action-agent participation.

## Ownership and boundaries

- All public requests reach Caddy on ec2dev first. Caddy access records are
  therefore the traffic source of record, ahead of Apache, `swf-remote`, or
  any application tunnel that replaces the client identity.
- The collector, database models, Snapper provider, Capcom producer, and cron
  declaration live in `tjrepo/tjai`.
- Caddy configuration lives in `tjrepo/ops/ec2dev/caddy`; changes are applied
  through the ingress runbook there.
- No SWF-side code, deployment, or endpoint is required. SWF components may
  later publish their own cache and authentication observations; their absence
  does not affect this monitor.
- The collector observes and reports. It changes no Caddy, firewall, or
  application policy. Enforcement is a separate, explicitly reviewed ingress
  change informed by this monitor's history.

## Runtime

The collector is a standalone script, `scripts/cron/collect_ec2dev_web.py`,
using the shared `scripts/bootstrap.py` Django setup like the other cron
scripts. The admin crontab runs it from the deployed tree every minute:

```text
* * * * * flock -n /tmp/ec2dev-web.lock \
  /var/www/tjai/.venv/bin/python \
  /var/www/tjai/scripts/cron/collect_ec2dev_web.py >> /tmp/cron_ec2dev_web.log 2>&1
```

The script self-paces from its recorded last-run time: in normal mode a tick
that arrives less than two minutes after the last completed run exits
immediately, giving a two-minute collection cadence; in incident mode every
tick runs, giving one-minute cadence. The mode flag lives in the database, so
cadence changes require no crontab edit. `flock -n` skips an overlapping
invocation rather than accumulating collectors.

## Data path

```text
Internet
  -> Caddy structured access records (journald)
  -> collect_ec2dev_web.py (1-minute cron, self-paced)
       -> 15-minute source and route aggregates in PostgreSQL
       -> bounded ec2dev-web component state in Snapper
       -> aligned Snapper capture
       -> ec2dev-web Capcom tile and transition notices
```

Caddy is configured to emit structured JSON access records for every public
site to the systemd journal. The admin user reads the journal through its
existing `adm` membership, so no world-readable log file or additional
privileged process is needed. Operational Caddy messages share the journal and
are ignored; only records whose logger identifies an HTTP access record are
consumed.

The collector stores the last committed journal cursor. It reads strictly
after that cursor and commits aggregate updates and the replacement cursor in
one database transaction, so a failed run persists nothing and safely replays
the same input on the next tick. If the saved cursor has fallen out of the
journal, the collector records a coverage gap rather than treating the next
visible record as continuous history.

Snapper publication runs after the traffic transaction commits and reads the
stored aggregates. A publication failure does not disturb the cursor; the next
run republishes the same boundary.

## Adaptive cadence and incident mode

Snapper capture is change-driven: a snap is recorded when published component
content changes canonically, with periodic baselines bounding quiet
intervals. The collector exploits this by varying the resolution of what it
publishes:

- **Normal mode** publishes a quantized projection — counts, rates, and
  concurrency rounded to coarse steps. Routine traffic produces identical
  canonical content run after run, so the quiet timeline costs a handful of
  snaps per hour plus baselines.
- **Incident mode** publishes exact values every one-minute run, producing a
  one-minute snap timeline for the duration of the incident.

Incident mode is entered when a trip condition crosses its threshold: request
rate, near-concurrent request overlap, route fan-out, 5xx rate, or backend
saturation. It exits when all conditions have remained clear through a
cooldown period, preventing flapping. Thresholds, quantization steps, and the
cooldown are versioned sysconfig values; each published component records the
projection version in effect.

The mode transition is a single concept with three consumers: it switches
collection cadence and publication resolution, it sets the Capcom tile color,
and it emits the incident-opened and incident-closed notices. There is no
separate detection path for any of the three.

## Recorded data

Drilldown history is kept at 15-minute aligned intervals regardless of
collection cadence; each run folds its batch additively into the current
interval row under a natural uniqueness key. Fine-grained development is
carried by the Snapper snap history, not by finer aggregate rows.

Three TJAI-owned tables:

- `WebTrafficCursor`: journal cursor, last source timestamp, last completed
  run, current mode, coverage-gap state, and parse and error counters.
- `WebTrafficSourceInterval`: per-interval, per-source aggregates carrying
  request count, peak overlap, route fan-out, user-agent family, and
  automation classification. Each interval retains the top sources by request
  count plus one residual row aggregating the remainder, so a crawl rotating
  through many addresses cannot inflate the table.
- `WebTrafficRouteInterval`: per-interval, per-service, per-route-family
  aggregates carrying request counts, response classes, bytes, and duration
  summaries (p50, p95, max).

The service and route-family registry is a sysconfig key seeded by migration
and editable in place, beginning with the ec2dev ingress map (`/prod`, `/doc`,
`/tjai`, `/primus`, `/pax-eden`, `/kozy`, and the domain-root applications).
Query strings are discarded before persistence. Dynamic path segments are
normalized so fan-out measures movement across application surfaces, not many
IDs within one route.

Caddy logs a request when it completes. Route families carrying long-lived
connections — the websocket and streaming endpoints (`/ws`, `/lkws`,
`/kozy`) — are marked in the registry: their requests are counted on close
and excluded from duration percentiles and concurrency reconstruction, which
would otherwise be distorted by connection lifetimes.

Aggregate history is retained indefinitely. Raw access records stay under
journald's rotation and are not copied into PostgreSQL. Any future roll-up or
retention policy requires measured scaling evidence and an explicit design
change.

### Source identity and privacy

The database does not retain client IP addresses. A source is a stable keyed
HMAC of the normalized address using a TJAI deployment secret, preserving time
history and per-source drilldown without storing the address. The collector
retains a bounded user-agent value and its normalized family as operational
evidence. It never stores Cookie, Authorization, or other credential headers,
and it does not infer login state from cookies: the authentication class is
reported as unknown until an application deliberately exports a safe
annotation.

## Automation classification

- `declared-bot`: a user-agent signature from the committed signature set
  identifies a crawler or automation client.
- `suspected-automation`: near-concurrent requests and broad normalized-route
  fan-out from one source cross a versioned policy threshold. Repeated sweep
  shape and periodic recurrence strengthen the evidence.
- `unknown`: the available evidence does not establish automation.

A user-agent match alone yields only `declared-bot`. High volume alone does
not make a source `suspected-automation`; the classification requires both
concurrency and fan-out, the probe shape that can occupy a backend worker
pool. Signatures and thresholds are sysconfig values. The first deployment is
observational: it records which sources would trip the policy and surfaces
them in Snapper and Capcom. Blocking or challenging a class is a separate
ingress policy decision.

## Backend load

Request timestamps and durations reconstruct overlapping in-flight requests
globally, per service, and per source within each collection batch. This is
the primary historical measure of load arising from traffic. Each working run
also samples Apache's local `server-status?auto` endpoint and host load and
memory, recording busy and idle workers, connection state, and system load as
capacity context. Service capacity metadata in sysconfig lets the display
express observed concurrency as a fraction of the relevant backend pool.

## Snapper contract

TJAI hosts Snapper: the `snapper_ai` package is installed in the TJAI Django
project, its tables live in the TJAI PostgreSQL database, and its UI is
mounted on the authenticated TJAI surface. The scope is `ec2dev-web`, with
its report at:

```text
https://etaverse.com/tjai/snapper/ec2dev-web/report/
```

The collector is the scope's only writer: it publishes components, then
invokes the aligned capture in the same run. No separate capture scheduler
process exists.

Four components:

- `traffic`: ingress-wide request rate, bytes, response classes, peak
  overlap, and distinct-source count.
- `services`: per-service traffic, latency, errors, and capacity fraction.
- `automation`: declared-bot volume, suspected-automation source count, and a
  bounded list of the most significant source signatures.
- `collector`: mode, journal lag, last complete interval, coverage gaps, and
  parse failures.

The provider registers Traffic, Responses, Load, and Automation curve
families and a per-service focus view. Source and route drilldown for a
selected window reads the dedicated interval tables; unbounded source and
path lists are never embedded in component state.

## Capcom contract

`ec2dev-web` is one `kind=state`, `mode=listen` Capcom source. The collector
writes the tile in process, so the tile's dot-menu update action is the
standard listen-source no-op with an explanatory message. The tile links to
the Snapper report.

- Green: telemetry is current and no service shows elevated load, a strong
  automation signature, or a material failure pattern.
- Yellow: incident mode is active for an elevated-load or automation
  condition, or telemetry is partially impaired.
- Red: telemetry is blind or stale, a backend is at saturation risk, or
  traffic is producing material service failures.

Routine collection is silent. Notices are emitted only for incident-mode
entry and exit and for newly established telemetry problems, each with a
stable deduplication key, so an ongoing condition threads into one feed row.

## Delivery slices

1. Caddy structured access logging via the ingress runbook, the three tables,
   the collector script, and the one-minute cron entry.
2. Snapper installed in TJAI, the `ec2dev-web` provider and components, and
   the authenticated report page.
3. The Capcom registry row, tile producer, and transition notices.
4. Threshold and quantization calibration from captured traffic; any
   enforcement change follows as a separate reviewed ingress operation.

Deployment validation: one cron run advances the cursor and its aggregate
interval, one Snapper boundary is current, and the Capcom tile links to the
report.
