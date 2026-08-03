# Capcom

Capcom is a live notice page: a single reverse-chronological feed of notices
from the systems and activities the user follows, with persistent read/unread
state, beside a panel showing current system state and pinned bookmarks. It
is served at `/tjai/capcom/`. The name is the Mission Control capsule
communicator (CAPCOM), the one console permitted to speak to the crew: only
deliberate, curated emissions from followed systems reach the feed.

The page, notice store, ingest endpoint, dispatcher, and in-process TJAI
emission hooks are implemented. External collectors and listen hooks land
one at a time.

## Purpose

Existing tjai views are retrospective (the daily synopsis assesses the
previous day), source-organized (the RSS and Picks pages), or forensic (the
Agent Log). None shows what has happened across followed systems since the
last look. Capcom fills that gap with the mail-inbox model: one linear feed
of heterogeneous items, an unread count that says whether anything needs
attention, and per-item read state so nothing scrolls away unseen. The
distinction from the RSS page is structural — the RSS page organizes items
by source; Capcom is one stream with filtered views over it.

## Layout

Two panels:

- **Left panel — state and pins.** Current-state tiles at the top: compact
  indicators for sources with a meaningful present state (service health,
  testbed and production activity, the Ahbazon gate camp). Tile values are
  read from a `capcom_state` sysconfig key that sources update alongside
  their event posts, so state never derives from feed rows. Below the tiles,
  a pinned-bookmark shelf listing bookmark entries carrying the `:pin` tag.
  Pinning by tag means any bookmark can be promoted to or removed from the
  shelf from wherever it is displayed, there is no separate curated document
  to maintain, and the capture flows (Chrome extension, MCP) are unchanged.
  The shelf has a hand-ordered top group over a reverse-time remainder:
  pins whose UUIDs appear in a `capcom_pin_order` sysconfig key render
  first, in key order, and the remaining pins follow in reverse time order.
  A synthetic `Diary - <current date>` pin is always first and opens the
  current diary entry directly in edit mode in a new tab; it is independent of bookmark
  membership and pin ordering.
  State tiles are click-drag ordered through the `capcom_state_order`
  sysconfig key. An enabled poll tile has a compact three-dot menu with one
  action, **update**, which forces only that state source; grouped sources
  still share their transport fetch, but only the selected returned state is
  applied. Linked tiles continue to open their source page in a new tab.
  A pin-to-top control adds a pin to the key; drag reordering within the
  group rewrites the key through `set_sysconfig` per the dashboard
  preference pattern (see [Dashboard](dashboard.md)). Top-group membership
  and order both live in the sysconfig key — the bookmark entry carries
  nothing beyond `:pin` — so ordering touches no entry state and needs no
  new server endpoint. Key UUIDs whose entries are no longer pinned are
  ignored at render and dropped on the next write. Duplicate UUIDs are
  collapsed on render, insertion, and drag save; feed refreshes do not
  rerender the group while a drag is active.
- **Right panel — selectable views.** A row at the panel's top left selects
  the view, carried in the URL. **Feed** (default): reverse-chronological
  notices with read/unread rendering, filter controls, an unread count, and
  an Update button at the panel's top right that runs all poll sources
  immediately. **Config**: the source registry, displayed and edited in
  place. Registry changes save automatically.

The feed carries events; the tiles carry state. Collectors emit a notice on
a state transition (camp up, run finished), not while a condition persists,
and the current condition is always readable from the tiles. This division
keeps the feed quiet enough to trust.

## Notice model

Notices are high-volume operational rows and live outside the entry system,
following the precedent of `RssItem` and `AppLog`. A `Notice` row carries:

- `timestamp` — last update
- `first_seen`
- `source` — registry key of the emitting system or collector
- `severity` — informational through alarm
- `title` — the one-line notice text
- `url` — deep link into the page or system the notice concerns; optional,
  and never self-referential — link-free notices are a normal category
- `was_read`
- `archived`
- `dedup_key` — groups repeated notices on one ongoing condition
- `count` — occurrences threaded into the row

Threading is applied at ingest: a notice whose `dedup_key` matches an
unarchived row updates that row — the timestamp and count advance and
`was_read` clears — rather than inserting a new one. A read notice remains
in the feed greyed out; `archived` is an internal threading boundary with
no interface surface. Old notices are purged on a retention schedule, as
entry versions are.

The feed is deterministic — no ranking or model-based selection. Curation
happens at the source level: a source is either admitted to the registry or
it is not.

## Collection

Ownership determines how a source is collected. Systems maintained within
this ecosystem — the tjai pipeline, corun-ai, primus, pax-eden — are never
polled: each registers as a source and calls the ingest endpoint at the
moment an event occurs. Polling is reserved for systems that cannot be
instrumented; their collectors are standalone scripts reading public
endpoints.

All notices pass through one implementation: an `emit_notice()` helper
holds the threading and dedup logic. The bearer-authenticated
`POST api/capcom/notice` endpoint wraps it for posting systems and
collectors; in-process emitters such as the overnight pipeline call it
directly. Second Life LSL scripts reach the endpoint via `llHTTPRequest`
with no intermediary.

Poll sources run from a dispatcher on a ten-minute periodic action. Each
source declares its cadence in the registry as a multiple of the tick; the
dispatcher runs the sources that are due and records scheduled last-run
times back in the registry. A state tile's Update command queues a separate
request that the action agent services immediately. Manual refreshes update
the tile without changing either the source or dispatcher periodic clock;
listen sources have no on-demand action.

### Source registry

The registry lives in a `capcom_sources` sysconfig key, displayed and
edited in the Config view as two sections: **feed** sources, which emit
notices, and **state** sources, which maintain a left-panel tile. A
registry row carries its kind, its collection mode, plus — for poll
sources — cadence, an enabled flag, and last-run time. The feed section
also shows each source's notice count over the trailing 24 hours. Row
deletion asks for confirmation and saves immediately:

- **listen** — the source posts to the ingest endpoint
- **poll** — a collector polls a read endpoint
- **missing** — the system exposes no usable status endpoint or hook

A system may register any number of sources: separate subscriptions — for
example several SWF feeds, or visitor sensors at different Second Life
places — are separate registry rows, each with its own source name. The
source name is the join key: notices, state tiles, and (for poll sources)
the dispatcher's collector table all reference it. Multiple state rows
returned by one endpoint may share a `collector` key; the dispatcher then
fetches that endpoint once and advances every grouped row's last-run time.

The `missing` state is deliberate: it records where a followed system needs
a status endpoint or callback that does not yet exist, so the gap is tracked
as a request to make of that system rather than worked around.

### Contract with sources

Capcom consumes what systems deliberately expose or emit. It does no
failure detection of its own: monitoring, watchdogs, and failure tracking
belong to the systems themselves, which may emit a curated alarm that
Capcom carries as a notice like any other.

## Initial sources

- **ePIC/SWF** (poll, state) — one swf-monitor endpoint returns complete
  Capcom payloads for `swf-system` (the System page verdict) and `swf-panda`
  (running jobs and 12-hour success percentage). Both registry rows share
  the `swf-monitor` collector, so the tunnel endpoint is fetched once per
  cadence and each returned entry is stored verbatim with `set_state(**entry)`.
  Section failures are source-owned `UNAVAILABLE` tiles; transport or contract
  failures raise a collector warning. The Mattermost feed subscription remains
  future work and is unrelated to these state tiles.
- **TJAI pipeline** (listen, in-process) — completion notices for research
  syntheses, Picks, the daily synopsis, ideation, AI performance
  assessments, and the weekly workweek summary. Each notice deep-links to
  its output. A producer emits one warning only after a terminal failure;
  retries and individual research-model completions do not emit notices.
- **TJAI system** (listen, in-process) — a notice when aggregate system
  health changes between green, yellow, and red. Routine health collection
  does not emit. The transition notice carries the current causes and links
  to the System page.
- **corun-ai** (listen) — a notice when an interactive run is submitted,
  emitted from the submit path of the registration mechanism; runs
  submitted through the programmatic REST interface do not pass that point
  and generate no notice.
- **EVE Online** (poll, state) — the `eve-ahbazon` tile shows `CLEAR`,
  `ACTIVITY`, or `GATECAMPED`, mapped from Pax Eden's green, amber, and red
  logic for the Ahbazon side of the Hykkota and Lor gates only; the extra
  Shera gate and the reverse sides in Hykkota and Lor do not affect the tile.
  Pax Eden owns the gate selection, severity mapping, display value, color,
  and destination URL, returning a complete Capcom state payload. The Capcom
  dispatcher invokes that production Pax Eden producer every ten minutes and
  stores the returned payload verbatim. The tile links directly to that
  route's Pax Eden gatecheck page.
- **Second Life** (listen) — visitor presence at monitored places via
  primus. Live data gathering there does not exist today (the capability
  is in legacy LSL scripts); this is the motivating case for direct posts
  to the ingest endpoint.
- **Curated alarms** — alarm-severity notices from systems that do their own
  monitoring, as they add that capability.

## Excluded by design

Candidate sources and features considered and not adopted:

- **Raw failure and watchdog streams** — noise; only curated alarms are
  carried, per the contract above.
- **Git and development activity** — already sufficiently visible through
  existing views and workflow.
- **Calendar and todos** — each has its established home (the dashboard
  calendar; the priority page). Capcom does not duplicate them.
- **Weather alerts and KozyKorner presence** — neither needs a place here;
  KozyKorner already has higher visibility on its own.
- **Push alerting** — no outbound push channel (Telegram or otherwise) is in
  the design. Whether alarm-severity notices should reach another channel is
  deferred until the feed demonstrates the need.
- **Importance ranking** — the one mail-inbox feature not copied; the feed
  stays deterministic and complete.

## Feed mechanics

- Unread count in the page title, visible on the browser tab.
- A row click marks the notice read and, when the notice carries more than
  its row shows — a detail body in `data.detail`, or threading history on a
  coalesced item — expands that inline; clicking the row again or the
  expansion itself collapses it. The title of a linked notice opens its
  deep link in a new tab; a link-free notice is a normal category and
  renders as plain text.
- Each row carries a mark-read / mark-as-unread toggle immediately after
  the title, at row font size in a visible color; mark-all-read and
  unread-only controls sit in the header.
- Read notices grey out, the whole line.
- `j`/`k` keyboard navigation through the feed.
- Filters (source, severity, read state) encoded in the URL, following the
  dashboard's URL-as-state convention, so every view is bookmarkable.

## Files

- `tjai_app/models.py` — `Notice` model (`capcom_notices` table)
- `tjai_app/capcom.py` — `emit_notice()`, state tiles, source registry,
  retention purge
- `tjai_app/views.py` — `capcom_page`, `api_capcom_feed`, `api_capcom_mark`,
  `api_capcom_notice` (ingest), `api_capcom_run`
- `tjai_app/templates/tjai_app/capcom.html` — the page (feed and config
  views, tiles, pinned shelf)
- `tjai_app/static/tjai/sortable.min.js` — vendored SortableJS for pin drag
- `scripts/capcom_dispatcher.py` — poll dispatcher, run by the
  `capcom-dispatcher` action (periodic, 10 minutes)
- `scripts/agent_complete.py`, `scripts/assessment_claude.py`,
  `scripts/assessment_gemini.py`, `scripts/workweek_agent.py`, and
  `scripts/system_health.py` — in-process TJAI completion, terminal-failure,
  and system-transition emitters
- `tjai_app/action_runner.py` — terminal mechanical and dispatch failures
  from named Capcom producers
- `scripts/capcom_test.py` — functionality test (emit, threading, purge)
