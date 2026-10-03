# Inflight todos

An inflight todo is the working record of an activity in progress: a todo
entry with status `inflight` whose body carries the activity's description,
its open items, and the references a session needs to pick it up. The set of inflight todos is the list of activities currently being
worked, by the user and by LLM sessions, and each one is visible and
editable to all of them at once. Todos in other statuses are the user's
backlog and are not touched by this machinery.

## Shape

```
<title: the activity, one line>

<description: goal, scope, where it stands>

## Live
- open item
- open item
  an indented line continues the item above

## Refs
- [design doc](url)
- [PR](url)
```

`## Live` holds open items, one per line starting with `- `; finishing an
item takes it out. `## Refs` lists what a session reads to bootstrap on the
activity. Indented lines continue the item above them. No other markup
carries meaning; the `!!!` marker keeps its usual sense inside an item when
the user chooses to use it. Missing sections are created when first needed.

The body contains task state: actionable unfinished items in Live and
supporting references in Refs. **There is no Done.** Every session that takes
up an activity reads its body whole, so finished work kept there would be
carried into every one of them; what is done is recorded in git and the
project's documents, and a session learns it there. Work built but waiting
on the user's check stays in Live until it is confirmed, then is taken out.
An older body's `## Done` section is still parsed (`. ` lines), and the
switch that kept one (`inflight.py` `KEEP_DONE`) is off, with reopening and
the done count. Session
handoff notes, cycle summaries, restart instructions, closeout/checkpoint
snapshots and cumulative implementation logs are not stored in TJAI entries.
Session continuity uses the client's session history or recorded dialog.
Captured dialog and existing version history remain historical records.

## Closing

**Inflight is transient**: the fast record of what is being worked now, never an
archive. An activity closed as done is deleted (softly, its versions kept) in
the same write, whichever path sets the status (`signals.py`
`retire_closed_activity`, switch `inflight.py` `RETIRE_CLOSED`): a todo marked as
an activity (`data.activity`) or one that was inflight until that save. A parked
activity is `blocked` and stays. What was done is recorded in git and the docs.
`scripts/retire_done_activities.py` retired the activities closed before the
rule.

## Surfaces

The inflight surfaces are a view of the Capcom page, in its right panel with
the left panel (state tiles, shelves, pins) intact: `/tjai/capcom/?view=inflight`
is the index and `&id=<entry_id>` one activity, the same URL-carried view
state as the pads. The panel hosts an iframe whose content is served by
`/tjai/inflight/` and `/tjai/inflight/<entry_id>/` with `?embed=1`; a direct
visit to those paths redirects into the Capcom view. Navigating inside the
frame (index to activity and back) is mirrored into the address bar.

- **Index** lists the inflight todos, most recently touched first, with
  open counts and time since last change, and below them a
  **Completed** section, dimmed: an activity with nothing outstanding,
  whatever its status, together with the parked ones (closed ones are
  deleted, § Closing). Those
  carry `data.activity`, stamped while the todo is inflight — a nightly agent
  product is a todo with the same sections, entry_id shape and tag, so
  nothing in a closed body distinguishes the two and the mark has to be set
  in advance. Each opens its live view in place; Refresh re-reads the list.
- **Live view** renders one activity:
  description, Live items each with a done button (which takes the item
  out), an add-item field, and Refs. Every action is a surgical edit
  of the current content, serialized per entry, attributed
  `inflight:<user>` in the version history. Refresh re-reads the
  entry's state and re-renders it if anyone has changed it, from the editor,
  from MCP, or from another live view. The view does not refresh itself: it
  is not watched continuously, so a timer asking on its own bought nothing. Edit opens the ordinary entry editor in a new tab, which
  returns to the Capcom inflight view on save.
- **Capcom** also shows an Inflight shelf on the left panel, one line per
  inflight todo with its open count and age; a click opens that activity in
  the right panel.
- **Entry editor**: an inflight todo's page carries a live-view link, and
  while it is being edited the page polls for changes by others and shows
  who changed it and when. The editor sends a hash of the content it
  loaded with every save; if the stored content still matches, the save
  is applied as is, and if it differs the save is merged three-way (the
  version snapshot carrying that hash, the editor's text, the current
  content), line by line: changes on one side
  only are taken, identical changes collapse, two insertions at the same
  point keep both, and any other overlap is written into the content with
  `<<<<<<< yours` / `=======` / `>>>>>>> server` markers for the user to
  resolve; the editor announces the conflict and the live view shows a
  warning until the markers are gone. Other entries keep the editor's
  existing line-union merge.

## LLM sessions

`get_todos(status='inflight')` returns the activities in progress with
the usual content preview; `max_content_length=0` requests full content.
At session start, `get_todos(status='inflight', summary_only=True)` returns
the complete activity index with identifiers, titles, contexts, statuses
and open counts. A session starting on an activity reads it in full,
then reads its Refs and
works from its Live list; it marks items done (taking them out) and adds
items with the `inflight_item` MCP tool (the web item endpoint's twin; both
run `services.inflight_item`), or for other edits the surgical entry tools
(`replace_text_in_entry`, `append_entry_content`), never by replacing the
whole content.

An LLM may create a todo on its own initiative only with status
`inflight`, may edit only inflight todos, and only surgically. Every other
todo write happens on the user's explicit instruction. The status values
accepted everywhere are `active`, `inflight`, `done`, `blocked`, `archive`,
`failed`; a parked activity is `blocked`, and a finished one is set `done`,
which deletes it (§ Closing).

## Files

- `tjai_app/inflight.py` — parse, `summary`, `mark_done`, `reopen` (off),
  `add_item`, `three_way_merge`
- `scripts/strip_inflight_done.py` — takes the Done section out of every
  inflight activity, one surgical edit each
- `tjai_app/services.py` — `inflight_item`; `tjai_app/mcp.py` — the
  `inflight_item` tool
- `tjai_app/views.py` — `inflight_page`, `api_inflight_list`,
  `api_inflight_state`, `api_inflight_item`, the Inflight shelf in
  `api_capcom_feed`, the three-way branch in `api_entry_save`
- `tjai_app/templates/tjai_app/inflight.html` — index and live view
- `tjai_app/templates/tjai_app/entry_detail.html` — live link and change
  banner for inflight entries
- `scripts/test_inflight.py` — functionality test (pure functions);
  `scripts/test_inflight_web.py` — end-to-end through Django's test client
  (pages, item actions, state poll, Capcom shelf, editor merge);
  `scripts/test_inflight_browser.py` — real-browser click test through the
  Capcom frame (CSRF, frame, live re-render), run with the playwright venv
