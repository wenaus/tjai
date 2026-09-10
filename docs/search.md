# Search

Full-text entry search: the index, the tokenization rules, query syntax,
ranking, and date filtering. The client-side behavior of the dashboard search
box is in [dashboard.md](dashboard.md).

## Surfaces

Three surfaces query the same index:

- `services.search_entries` — the service function, exposed as the
  `search_entries` MCP tool and as a Telegram bot tool (`tg_bot/tools.py`).
- `api/dashboard/search` (`views.dashboard_search`) — the dashboard search
  endpoint, which constructs its query directly.

## Index

`entries.search_vector` is a `tsvector` column with a GIN index
(`entries_search_gin`). A database trigger (`entries_search_vector_trigger`,
migration 0013) recomputes it on every insert and on every update of
`content`, via `entries_search_vector_update()`:

```sql
NEW.search_vector := to_tsvector('english', translate(COALESCE(NEW.content, ''), '/', ' '));
```

The `english` configuration stems words: "computing" matches "computed" and
"computation".

### Slash normalization

Slashes are translated to spaces before vector construction (migration 0025),
and `services.fts_normalize` applies the identical replacement to query text
at both query-construction sites. Without it, Postgres lexes a slashed
compound such as `Prod/testbed` as a single file-path token, which no word
query can match. With it, both sides tokenize the same way and "testbed"
matches "Prod/testbed meeting". Any change to content-side tokenization must
be mirrored in `fts_normalize` and requires a vector rebuild
(`UPDATE entries SET search_vector = ...`, as in migration 0025).

## Query semantics

Queries compile with `websearch_to_tsquery`: quoted phrases
(`"streaming workflow"`), exclusions (`-test`), and `OR`. A malformed
websearch query falls back to plain parsing. A query containing no word
characters (for example the `!!!` todo marker) compiles to an empty tsquery
and would match nothing; such queries are searched as literal substrings
(`content ILIKE`) instead.

The dashboard endpoint additionally accepts `field=title`, which restricts
full-text matches to entries whose first content line contains each query
word as a substring.

## Ranking

`order_by='rank'` sorts by `ts_rank_cd` (cover density, normalized by
document length), then recency. `order_by='time'` (default) sorts by
modification time; `order_by='size'` by content length.

## Date filtering

`search_entries` accepts `start_date`/`end_date` (`YYYYMMDD`, `YYYY-MM-DD`,
ISO timestamps such as `2026-09-10T17:08:00Z`, or natural language such as
`7d`, `yesterday`, `monday`). Explicit timestamps preserve their time and
offset; date-only bounds span 00:00:00 through 23:59:59 in the configured
application timezone. See [MCP date filters](mcp.md#date-filters) for accepted
formats and timezone rules. Which timestamp they filter on is controlled by
`date_field`:

- `modified` — `timestamp_modified`.
- `event` — the calendar placement in `data.event_date`; entries without one
  are excluded.
- `auto` (default) — `event` when `kind='journal'`, otherwise `modified`.
  Journal entries live on the calendar, so a date-scoped journal query means
  the event date.

Functional checks for tokenization, calendar output, and date filtering are
in `scripts/test_mcp_tool_fixes.py`, run read-only against the live database.
