# Dashboard

The dashboard is the main tjai web page: a live calendar, named-entry index, status summary, and filterable entry list. It is served at `/tjai/` and refreshes every 20 seconds. The entire client is one Django template, `tjai_app/templates/tjai_app/dashboard.html`, holding inline CSS and one `<script>` block. There is no build step and no client framework; rendering is hand-written string templating against JSON fetched from a small set of read endpoints. Shared helpers come from `tjai_app/static/tjai/tjai-utils.js`.

This document describes the client side: page modes, the URL-as-state model, the data-fetch cycle, each panel, filtering and search, the mutations the page can perform, and the server endpoints it depends on.

## Page modes

The same template renders four modes. The Django `dashboard` view sets three booleans from query parameters, and the script reads them at load:

| Mode | Trigger | Django context | Client constant |
|------|---------|----------------|-----------------|
| Normal | (default) | — | all three false |
| Archive | `?view=archive` | `is_archive` | `isArchiveMode` |
| Dialog | `?view=dialog` | `is_dialog` | `isDialogMode` |
| Trash | `?deleted=1` | `is_trash` | `isTrashMode` |

`isSimplifiedMode` is the OR of the three. Simplified modes hide the left panel (calendar and named entries) and the calendar/named fetches are skipped. Normal mode shows both panels.

The view redirects `?status=archive` to `?view=archive` so a status filter for archived entries lands in archive mode.

## Layout

Two panels side by side (`.container` flexbox):

- **Left panel** (normal mode only): the calendar section (`#calendar-content`) over the named-entries section (`#named-content`).
- **Right panel**: a fixed status block (`#status-fixed`), an optional dialog chart, the entries header with the search box, and the scrolling entries list (`#recent-entries` → `#entries-list`).

The page uses a dark theme with a monospace font. Colors are fixed hex values inline; entry kinds, contexts, tags, and links each have a consistent color.

## State model: the URL is the state store

All filter and view state lives in module-scoped variables (`activeTags`, `activeKind`, `activeContext`, `activeStatuses`, `excludedStatuses`, `excludedContexts`, `activeMachine`, `activeClient`, `activeModel`, `activeDate`, `activeFromTime`, `activeToTime`, `activePublic`, `activeWithRelations`, `expandDialog`, `dialogView`, `expandedRelations`, `isSearchActive`, `namedSort`). Three functions keep these in sync with the query string:

- `syncStateToUrl()` serializes the active state into query parameters and calls `history.replaceState`. It replaces rather than pushes, so filter changes do not fill the browser history. It runs after every state change.
- `loadStateFromUrl()` runs once at load, parsing the query string back into the state variables. This makes any dashboard URL bookmarkable and shareable: the filters, the dialog/archive view, expanded relations, and an active search all reconstruct from the URL.
- `buildFilterParams()` produces the parameter list sent to the server on each fetch. It overlaps with `syncStateToUrl` but is the server-facing form (for example it emits `exclude_context` and `deleted=1`, which the URL form spells differently).

Query parameters and their state:

| Parameter | Meaning |
|-----------|---------|
| `tag` | CSV of required tags (AND); `_none` matches untagged entries |
| `kind` | entry kind filter |
| `context` | context filter |
| `exclude` / `exclude_context` | CSV of excluded contexts (URL / server spelling) |
| `status` | CSV of included statuses; `_none` is null/empty status |
| `exclude_status` | CSV of excluded statuses |
| `machine`, `client`, `model` | match `data.hostname`, `data.client`, `data.model` |
| `date` | restrict to one day (used by the dialog daily-count chips) |
| `from_time`, `to_time` | ISO datetime window over modification time |
| `public` | only entries marked `data.access=public` |
| `with_relations` | only entries that participate in a relation |
| `expand_dialog` | render full content instead of the first line |
| `rel` | CSV of entry UUIDs whose relations are expanded |
| `view` | `archive` or `dialog` |
| `deleted` | `1` for Trash |
| `named_sort` | `mod` when the named panel is sorted by modification time |
| `search` | active search query |

Most state is reconstructed from the URL alone. The named-entries sort order is the one preference that also persists server-side (see [Named entries](#named-entries-panel)).

## Data-fetch cycle

`refresh()` is the heartbeat. It calls `fetchStatus()` always, and `fetchCalendar()` and `fetchNamed()` in normal mode, awaiting all of them in parallel. It runs once at load and on a 20-second interval, and is re-invoked whenever a change needs a full reload. The header shows time since last refresh and the last build time in milliseconds.

`fetchJson(url, options)` wraps `fetch`: it rejects when the response is not JSON or not OK, raising the server's `error` field as the message. Every fetch path catches and shows the failure in place rather than leaving a stale panel. A `window.onerror` handler writes uncaught script errors into the status block so they are visible on the page.

The page reads from these endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `api/dashboard/status` | GET | Status summary plus the first page of entries; the main poll |
| `api/dashboard/calendar` | GET | Calendar events |
| `api/dashboard/named` | GET | Named entries (sorted client-side) |
| `api/dashboard/search` | GET | Full-text entry search |
| `api/dialog/daily-counts` | GET | Per-day dialog-turn counts for the charts |
| `api/entry/<uuid>/relations` | GET | Relations for one entry, loaded on demand |

`api/dashboard/status` and `api/dashboard/search` paginate by `offset` with a page size of 1000. They carry no incremental-sync cursor: each poll re-fetches the head of the list, and older entries load by offset. When `offset > 0`, `api/dashboard/status` returns only `recent_entries`, `has_more`, and `total_count`.

## Calendar panel

`fetchCalendar()` initially fetches `api/dashboard/calendar` with no parameters and `renderCalendar()` builds the list. The client keeps the loaded calendar window in page memory only. Scroll near the top loads the previous 30-day slice with `before=<start_ts>` and preserves scroll position; scroll near the bottom loads the next 30-day slice with `after=<end_ts>`. Dashboard auto-refreshes reuse the expanded window with `start=<start_ts>&end=<end_ts>` so the calendar does not collapse back to the default range while the page remains open. A browser reload, new visit, or navigation away and back starts again from the default range. The server caps expansion to 180 days outside the default window and returns `has_older` / `has_newer` so the scroll loader stops at the bounds. The server returns events with pre-formatted Eastern-time fields (`date_display`, `time_display`, `date_key`, `week_num`, `week_start_key`) so the client does no date math for display.

Behavior:

- If the current day has no events, a placeholder is inserted so a "Today" marker always exists. On first render the list scrolls to put today a third of the way down; later refreshes preserve scroll position. The "Today" link in the top nav re-centers without reloading.
- The next future event gets a countdown chip. The chip stays on a just-started event and counts negative for up to 15 minutes after its start (`LATE_GRACE_SEC`).
- Clock entries (`data.clock` = `start`/`stop`), annual events (`data.annual`), daily synopsis entries (`daily-*`), and diary entries (`diary-*`) each render with their own color and link target. Daily entries link to the synopsis page; others link to the entry detail.
- When the browser's time zone differs from Eastern, an event's local time is shown in parentheses after the Eastern time.
- Selecting calendar rows and copying produces both plain text and HTML with the entry links preserved (`data-copy-line` plus a `copy` handler).

## Named entries panel

`fetchNamed()` fetches all named entries once per refresh; `renderNamed()` sorts and renders them. The panel has two sort buttons: alphabetical (the default) and modification time. Sorting is done client-side over the same data, so toggling does not refetch.

The chosen order persists in two places, checked in order at load:

1. A `named_sort` URL parameter, if present (so a shared link keeps its order).
2. The `dashboard_named_sort` sysconfig value, supplied by the server as the template default `SAVED_NAMED_SORT`.
3. Alphabetical, otherwise.

When the user toggles the sort, `setNamedSort()` writes the choice to sysconfig via `api/command` (`set_sysconfig`, key `dashboard_named_sort`). Because it is server-side, the order is remembered across visits and across devices. The `dashboard` view reads the same key and passes it into the template, so the first render already reflects it. See [Preferences](#preferences).

## Status panel

`renderStatus()` builds the fixed status block from the `api/dashboard/status` response. In normal mode it shows, top to bottom:

- A status line with the server timestamp and active context.
- The active time-range window, when `from_time`/`to_time` are set.
- **Contexts** — clickable to filter; `poetry` and `recipe` link to their own pages. A minus toggle on each excludes it instead.
- **Special tags** — `cool`, `daily`, `fave`, `my`, plus a free-text tag input.
- **Tags** — every non-context tag with a count, and a `(none)` option for untagged entries.
- **Types** — entry kinds with counts.
- **Todos** — open-todo counts per context.
- **Status** — status values with counts; clicking the name includes, clicking the minus excludes. `_none` shows as `(none)`.
- **Checkboxes** — Expand dialog, Public, With relations.
- **Machines** — hostnames with the oldest sync age.
- **Work sessions** — today's clock sessions with work and break totals.

Dialog mode replaces most of this with a client/model filter row and a per-day dialog-count row, and shows the daily and weekly bar charts (`updateDialogChart`). Chart bars have hover tooltips and link to that day's assessment page.

## Entries list

`renderEntries()` renders each entry as one line in the `tj l` style: a timestamp, a kind abbreviation, optional event date, context, name, the content (first line, or full when Expand dialog is on), a line count, and inline tags. Server-provided `date_display` is used directly; the client does not format entry timestamps. URLs and markdown links in content are linkified.

Each row carries hidden controls and a metadata bar:

- An **action select** (open / edit / archive / trash / restore / delete) whose options depend on the mode.
- A **Relations** button when the entry has relations. Expanding it loads `api/entry/<uuid>/relations` on demand and adds the entry to `rel` in the URL, so expansions survive a refresh and a reload.
- Inline **tag delete** (the `-` after each tag) posting to `api/entry/<uuid>/tag/<tag>/delete`.
- An **edit** link, a **restore** button (archive mode), and a **delete** button.
- A metadata bar built from the entry's `data` dict, formatting epoch and ISO timestamps and linking any `entry_id`-like value.

The list scrolls independently. Scrolling near the bottom triggers `loadMoreEntries()` (next offset page; search or status depending on mode). When a `to_time` window is set, scrolling to the top extends the window later; reaching the end of a `from_time` window extends it earlier by an hour.

Selecting entry rows and copying yields entry-aware clipboard data: plain text matching the display and HTML carrying each entry's UUID and kind, for paste into the entry editor.

## Filtering

Filter clicks are handled by event delegation on the status block. `toggleFilter()` flips the relevant state variable, updates the active styling, calls `syncStateToUrl()`, and calls `fetchFilteredEntries()`. Status and context support both include (click the name) and exclude (click the minus); a value cannot be both, and selecting one clears the other.

`fetchFilteredEntries()` re-runs the active search with the new filters when a search is active; otherwise it fetches `api/dashboard/status` with the filter params and re-renders, or does a full `refresh()` when no filters remain. The Expand-dialog and date-chip changes go through `refresh()` since they affect the whole view.

The **clear** button resets all filters and search and refreshes. Co-occurring tags are highlighted: when tags are active, other tags that appear on the filtered entries get an outline.

## Search

The search box runs `searchEntries()` against `api/dashboard/search`. Sort is chosen by the time/rank/size radios; `rank` uses full-text relevance, `size` orders by length, `time` is the default. Search respects all active filters (they are appended to the query) and paginates by offset like the entry list. Enter runs the search, Escape clears it, and clearing restores the filtered or unfiltered entry list. The entries header shows the query and result count while search is active.

## Mutations from the dashboard

The dashboard can change entry state. The delete/restore action depends on the mode, because the same button means different things in each:

| Action | Normal | Archive | Trash |
|--------|--------|---------|-------|
| Delete button (`X`) | archive the entry (`POST api/entry/<id>/archive`) | move to trash (`DELETE api/entry/<id>`) | permanent delete (`DELETE api/entry/<id>?hard=1`) |
| Action: trash | move to trash (`DELETE api/entry/<id>`) | — | — |
| Action: archive | — | — | re-archive from trash (`POST .../archive?from_trash=1`) |
| Action: restore | un-archive (`POST api/picks/update`, `archived=false`) | un-archive | restore from trash (`POST api/entry/<id>/restore`) |

Log pseudo-entries (`id` = `log-<pk>`) are always a direct `DELETE api/entry/log-<pk>`. Trash mode adds an **Empty Trash** button (`POST api/trash/empty`) behind a confirmation. The Diary, Entry, Workday, Workweek, and Create Entry buttons create or open entries through their own endpoints and navigate to the entry detail.

## Preferences

The dashboard reads and writes one sysconfig key for user preference:

- `dashboard_named_sort` — `alpha` or `mod`, the named-entry sort order.

It is read server-side in the `dashboard` view and written client-side through `api/command`'s `set_sysconfig` command. Future dashboard preferences should follow the same pattern: store under a `dashboard_*` sysconfig key, read it into the template context in the `dashboard` view for a correct first render, and write it through `set_sysconfig` on change. Sysconfig persists server-side, so a preference set this way carries across devices, where a URL parameter or browser storage would not.

## Dependencies

`tjai-utils.js` provides the shared client helpers the dashboard relies on:

- `escapeHtml(s)` — HTML-escape before any string interpolation.
- `linkifyContent(s)` — turn markdown links, wiki-links, bare URLs, and `:tags` in already-escaped text into HTML.
- `linkifyEntryReferences(s)` — link UUIDs and `entry_id=`-style references in log and metadata text.
- `entryRefUrl(ref)` — resolve a UUID or human-readable entry_id to its detail URL.
- `flashNotice(msg, color)` — the shared toast.
- A global `copy` handler that mirrors selections into the HTML clipboard channel and absolutizes relative links so they survive paste.

Date and time formatting for entries and calendar events is done server-side (`tjai_utils.py`); the client formats only live clocks and the few times that need the viewer's local zone.

## Server endpoints

Read endpoints feeding the dashboard, with their top-level response keys:

- **`api/dashboard/status`** (`dashboard_status`, GET, login required) — `timestamp`, `context`, `context_description`, `clock`, `work_sessions`, `recent_entries`, `has_more`, `total_count`, `timezone`, `contexts`, `all_tags`, `tag_counts`, `kind_counts`, `status_counts`, `status_options`, `open_todos`, `machines`, `oldest_sync`, `daily_counts`, `dialog_clients`, `dialog_models`. With `offset > 0`: only `recent_entries`, `has_more`, `total_count`.
- **`api/dashboard/calendar`** (`dashboard_calendar`, GET, login required) — `entries`, `server_time`, `start_ts`, `end_ts`, `default_start_ts`, `default_end_ts`, `has_older`, `has_newer`, `timezone`, `today_ts`, `today_date`, `today_date_display`. Query parameters: no parameters returns the default window; `before=<timestamp>` returns the previous 30 days; `after=<timestamp>` returns the next 30 days; `start=<timestamp>&end=<timestamp>` refreshes a bounded window.
- **`api/dashboard/named`** (`dashboard_named`, GET, login required) — `entries`, each with `id`, `name`, `content`, `context`, `timestamp`, `modified_display`, `line_count`. Sent with `Cache-Control: no-store`.
- **`api/dashboard/search`** (`dashboard_search`, GET, login required) — `entries`, `has_more`, `offset`, `total_count`.
- **`api/dialog/daily-counts`** (`api_dialog_daily_counts`, GET) — `daily_counts`.
- **`api/entry/<uuid>/relations`** (`api_entry_relations`, GET, login required) — `entry_id`, `relations`.

Command endpoint:

- **`api/command`** (`api_command`, POST) — JSON `{command, ...}`. Commands: `set_sysconfig` (`key`, `value`), `get_sysconfig`, `increment_json` (`key`, `field`). Returns `{status, result}`.

Mutation endpoints the dashboard calls: `api/entry/<id>` (DELETE, with `hard=1` for permanent), `api/entry/<id>/archive` (POST), `api/entry/<id>/restore` (POST), `api/trash/empty` (POST), `api/entry/<id>/tag/<tag>/delete` (POST), `api/picks/update` (POST), `api/diary/today` (POST), `api/entry/create` (POST).

Endpoint implementations are in `tjai_app/views.py`; routes are in `tjai_project/urls.py`. The general production and endpoint reference is in [Server & Deployment](server.md).

## Files

- `tjai_app/templates/tjai_app/dashboard.html` — the entire client: layout, styles, and script.
- `tjai_app/static/tjai/tjai-utils.js` — shared escape, linkify, toast, and clipboard helpers.
- `tjai_app/views.py` — `dashboard` (renders the template) and the `dashboard_*` / `api_*` endpoints.
- `tjai_project/urls.py` — route table.
