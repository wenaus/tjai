# Inflight todos

An inflight todo is the working record of an activity in progress: a todo
entry with status `inflight` whose body carries the activity's description,
its open and finished items, and the references a session needs to pick it
up. The set of inflight todos is the list of activities currently being
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

## Done
. finished item

## Refs
- [design doc](url)
- [PR](url)
```

`## Live` holds open items, one per line starting with `- `. `## Done` holds
finished items, one per line starting with `. `; finishing an item changes
its marker and moves it to the end of Done, reopening moves it back to the
end of Live. `## Refs` lists what a session reads to bootstrap on the
activity. Indented lines continue the item above them. No other markup
carries meaning; the `!!!` marker keeps its usual sense inside an item when
the user chooses to use it. Missing sections are created when first needed.

## Surfaces

- **Inflight page** (`/tjai/inflight/`) lists the inflight todos, most
  recently touched first, with open and done counts and time since last
  change. Each links to its live view.
- **Live view** (`/tjai/inflight/<entry_id>/`) renders one activity:
  description, Live items each with a done button, an add-item field, Done
  items each with a reopen button, and Refs. Every action is a surgical edit
  of the current content, serialized per entry, attributed
  `inflight:<user>` in the version history. The view polls the entry's
  state every three seconds and re-renders when anyone has changed it, from
  the editor, from MCP, or from another live view; a red dot means the poll
  is failing. Edit opens the ordinary entry editor and returns here on save.
- **Capcom** shows an Inflight shelf on the left panel, one line per
  inflight todo with its open count and age, linking to the live view.
- **Entry editor**: an inflight todo's page carries a live-view link, and
  while it is being edited the page polls for changes by others and shows
  who changed it and when. On save, if the entry changed since it was
  opened, the save is merged three-way (the version the editor opened,
  identified by a hash of the loaded content that the editor sends with the
  save, the editor's text, the current content), line by line: changes on one side
  only are taken, identical changes collapse, two insertions at the same
  point keep both, and any other overlap is written into the content with
  `<<<<<<< yours` / `=======` / `>>>>>>> server` markers for the user to
  resolve. Other entries keep the editor's existing line-union merge.

## LLM sessions

`get_todos(status='inflight')` returns the activities in progress with
their full content. A session starting on an activity reads its Refs and
works from its Live list; it marks items done, reopens them, and adds
items with the `inflight_item` MCP tool (the web item endpoint's twin; both
run `services.inflight_item`), or for other edits the surgical entry tools
(`replace_text_in_entry`, `append_entry_content`), never by replacing the
whole content.

An LLM may create a todo on its own initiative only with status
`inflight`, may edit only inflight todos, and only surgically. Every other
todo write happens on the user's explicit instruction. The status values
accepted everywhere are `active`, `inflight`, `done`, `blocked`, `archive`,
`failed`; a parked activity is `blocked`, a finished one `done`.

## Files

- `tjai_app/inflight.py` — parse, `summary`, `mark_done`, `reopen`,
  `add_item`, `three_way_merge`
- `tjai_app/services.py` — `inflight_item`; `tjai_app/mcp.py` — the
  `inflight_item` tool
- `tjai_app/views.py` — `inflight_page`, `api_inflight_list`,
  `api_inflight_state`, `api_inflight_item`, the Inflight shelf in
  `api_capcom_feed`, the three-way branch in `api_entry_save`
- `tjai_app/templates/tjai_app/inflight.html` — index and live view
- `tjai_app/templates/tjai_app/entry_detail.html` — live link and change
  banner for inflight entries
- `scripts/test_inflight.py` — functionality test (pure functions)
