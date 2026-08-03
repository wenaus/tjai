# Capcom

Capcom is a live notice page: a single reverse-chronological feed of notices
from the systems and activities the user follows, with persistent read/unread
state, beside a panel showing current system state and pinned bookmarks. It
is served at `/tjai/capcom/`. The name is the Mission Control capsule
communicator (CAPCOM), the one console permitted to speak to the crew: only
deliberate, curated emissions from followed systems reach the feed.

This is a design document; implementation has not started.

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
  testbed and production activity, the Ahbazon gate camp). Below the tiles,
  a pinned-bookmark shelf listing bookmark entries carrying the `:pin` tag.
  Pinning by tag means any bookmark can be promoted to or removed from the
  shelf from wherever it is displayed, there is no separate curated document
  to maintain, and the capture flows (Chrome extension, MCP) are unchanged.
  The shelf has a hand-ordered top group over a reverse-time remainder:
  pins whose UUIDs appear in a `capcom_pin_order` sysconfig key render
  first, in key order, and the remaining pins follow in reverse time order.
  A pin-to-top control adds a pin to the key; drag reordering within the
  group rewrites the key through `set_sysconfig` per the dashboard
  preference pattern (see [Dashboard](dashboard.md)). Top-group membership
  and order both live in the sysconfig key — the bookmark entry carries
  nothing beyond `:pin` — so ordering touches no entry state and needs no
  new server endpoint. Key UUIDs whose entries are no longer pinned are
  ignored at render and dropped on the next write.
- **Right panel — the feed.** Reverse-chronological notices with read/unread
  rendering, filter controls, and an unread count.

The feed carries events; the tiles carry state. Collectors emit a notice on
a state transition (camp up, run finished), not while a condition persists,
and the current condition is always readable from the tiles. This division
keeps the feed quiet enough to trust.

## Notice model

Notices are high-volume operational rows and live outside the entry system,
following the precedent of `RssItem` and `AppLog`. A `Notice` row carries:

- `timestamp`
- `source` — registry key of the emitting collector or poster
- `severity` — informational through alarm
- `title` — the one-line notice text
- `url` — deep link into the page or system the notice concerns
- `was_read`
- `dedup_key` — groups repeated notices on one ongoing condition

Repeated notices sharing a `dedup_key` thread into a single feed item that
returns to unread when it updates, rather than accumulating rows. Read state
follows inbox semantics: a read notice remains in the feed greyed out; an
archived notice leaves the feed but remains searchable. Old notices are
purged on a retention schedule, as entry versions are.

The feed is deterministic — no ranking or model-based selection. Curation
happens at the source level: a source is either admitted to the registry or
it is not.

## Collection

Collection is polling-first. Collectors are standalone scripts run from
cron, each polling a public read endpoint of a followed system and posting
resulting notices to the ingest endpoint. A bearer-authenticated REST ingest
endpoint is the single write path for notices: local collectors post through
it, and it also admits the few sources able to push directly — for example
Second Life LSL scripts via `llHTTPRequest` — with no collector in between.

### Source registry

Each source is registered with its collection mode:

- **poll** — a collector polls a read endpoint
- **push** — the source posts to the ingest endpoint
- **missing** — the system exposes no usable status endpoint or hook

The `missing` state is deliberate: it records where a followed system needs
a status endpoint or callback that does not yet exist, so the gap is tracked
as a request to make of that system rather than worked around.

### Contract with sources

Capcom consumes what systems deliberately expose or emit. It does no
failure detection of its own: monitoring, watchdogs, and failure tracking
belong to the systems themselves, which may emit a curated alarm that
Capcom carries as a notice like any other.

## Initial sources

- **ePIC/SWF** — testbed activity and production status via the swf-monitor
  REST API, and new ePIC Mattermost postings from the same source that feeds
  the synopsis, at live cadence.
- **Overnight pipeline** — completion notices for research syntheses, Picks,
  the daily synopsis, and ideation, each deep-linking to its page, so
  morning triage starts from one place.
- **EVE Online** — Ahbazon gate-camp status polled from the zKillboard API;
  the gate-checker logic exists in pax-eden.
- **Second Life** — visitor presence at monitored places via primus. Live
  data gathering there does not exist today (the capability is in legacy
  LSL scripts); this is the motivating case for the push path.
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
- Click marks read; a mark-all-read control; an unread-only toggle.
- Read notices grey out; archiving removes them from the feed.
- `j`/`k` keyboard navigation through the feed.
- Filters (source, severity, read state) encoded in the URL, following the
  dashboard's URL-as-state convention, so every view is bookmarkable.
