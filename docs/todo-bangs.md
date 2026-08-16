# Todo bangs — the `!!!` marker

A todo bang is a line opening with three or more exclamation marks, modulo a
markdown list prefix:

    - !!! review prmon directly for what should be in canary

It marks a todo in flow. The item is written where the thought occurred — a
diary entry, a project note, a design document — instead of being filed as a
separate todo entry, so capturing one costs nothing and interrupts nothing.
The `!!!` page at `/tjai/todo-bangs/` gathers every such line across the
knowledge base into one list.

The set is derived on every read. Nothing is stored, so nothing can go stale,
and there is no completion state to maintain: an item is finished by editing
the bangs out of its source line, after which it stops appearing. Bang count
is emphasis and is preserved as written.

## Extraction

One rule serves every consumer, in `services.todo_bang_entries()`:

- **Match** — `TODO_BANG_RE`, anchored at line start, allowing a `-`, `*`,
  `+`, or numbered list prefix.
- **Select** — `TODO_BANG_SQL_RE` is the SQL twin of that pattern and must
  stay literally identical to it. The partial index `entries_todo_bangs`
  (migration 0019) is defined on the same expression; if the two diverge the
  planner cannot prove the index applies and the query degrades to a full
  scan of the entry table.
- **Exclude** — recorded AI dialog, by both context and the `ccdialog` tag,
  plus archived and deleted entries. A bang line inside a dialog turn is a
  conversation artifact rather than one of the user's todos, and admitting
  those inverts the page's meaning: the exclusions currently drop about half
  the raw regex matches.
- **Order** — entries by modification time, newest first; lines within an
  entry in document order. Neither is re-ranked by bang count or recency,
  because position within a document carries the author's grouping.

## Surfaces

Two consumers read that extraction, and neither reimplements it.

The **page** adds rendering: each bang line's markdown is inline-rendered and
cached by line text, since the render costs about a millisecond per line and
bang lines rarely change. The first line of each entry carries the entry
title and modification date; subsequent lines carry their line number. Every
line links to its source line in the entry editor, and returns to the page
after the edit. Links embedded in a bang line keep right-of-way — a click
landing on a link follows it rather than opening the editor.

The **`get_todo_bangs` MCP tool** returns raw line text with the entry title,
`entry_id` URL, context, and modification date, plus entry and line counts.
Raw text is what an agent quotes into a report; rendered HTML is not.

## Consumers

The nightly ideation agent reads the bang set as its primary signal of live
work, alongside todo entries. The bang lines are hand-flagged by the user, so
they carry his own emphasis rather than an inferred priority. Because the set
is derived rather than versioned, ideation treats a persisting line as a
standing priority and not as news; a line that has disappeared marks completed
work.

## Files

- `tjai_app/services.py` — `TODO_BANG_RE`, `TODO_BANG_SQL_RE`,
  `todo_bang_entries()`, `get_todo_bangs()`
- `tjai_app/views.py` — `todo_bangs` (page), `api_todo_bangs`,
  `_todo_bang_entries()` (rendering), `_render_bang_line()`
- `tjai_app/mcp.py` — `get_todo_bangs` tool
- `tjai_app/templates/tjai_app/todo_bangs.html` — the page
- `tjai_app/migrations/0019_*` — the `entries_todo_bangs` partial index
