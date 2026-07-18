# CLI Command Reference

All commands use the `tj` function. Run `tj h` for a built-in summary.

## Agent Management

- `tj admin agent` — Show agent status (running, last sync time, interval)
- `tj admin agent start|stop|restart` — Control the sync daemon
- `tj admin agent install` — Install the daemon service
- `tj admin agent log` — Show recent agent log entries
- `tj admin agent sync` — Force full sync (reset and pull all)
- `tj admin agent location [name]` — Get/set machine location name
- `tj admin agent interval [seconds]` — Get/set sync interval (server-wide)

## Dashboard

- `tj` — With no arguments: status dashboard of recent entries, summary
  counts, backups, telegram bot and action agent status, and configuration

## List

- `tj l` — List recent entries
- `tj l 10` / `tj l -10` — Last / first 10 entries
- `tj l 7d` — Entries from the last 7 days
- `tj l c` — All contexts with entry counts
- `tj l t` — All tags with counts
- `tj l p/b/do/ai/log` — Profiles / bookmarks / todos / AI guidelines / logs
- `tj l priority` — All prioritized entries, sorted (p=1 first)
- `tj l archive` — Archived entries
- `tj l actions` — All action entries (numbered)
- `tj l --all` — No truncation (full content)
- `tj l --clean` — Content only, no preamble
- `tj a` — List with full content (same filters as `tj l`)

## Context

- `tj =tjai` — Switch to/create context (terse name only)
- `tj =tjai -t AI app development` — Create with title
- `tj =tjai -t AI app -d Personal project notes` — Create with title + description
- `tj =context <text>` — Create entry in specified context (overrides active)
- `tj =0` — Clear active context
- `tj =0 <text>` — Create context-free entry

## Create

- `tj <text> ...` — Default: creates a memory
- `tj m <text>` — Memory entry
- `tj do <text>` — Todo item
- `tj p <fact>` — Profile fact
- `tj ai <guideline>` — AI behavioral guideline
- `tj j <date/time> <content>` — Journal entry (yesterday, tomorrow, mon-sun, HH:MM, mmdd, YYYYMMDD)
- `tj <YYYYMMDD> ...` — Journal entry by date
- `tj <url> ...` — Bookmark

**Modifiers (work with any creation command):**
- `:tag` — Inline tag (auto-applies current context too)
- `@budget Q4 planning` — Named entry
- `p=1` — Priority (1=highest)
- `s=active` — Status (active, done, blocked, archive)
- `//https://url` or `[Title](https://url)` — Link
- `=tjai meeting notes` — Inline context switch + create
- `-f filename.txt` — Multi-line from file
- `tj e` — Opens $EDITOR for new entry
- `at=YYYYMMDD/HH:MM <content>` — Custom creation timestamp

## Modify

- `tj . <content>` — Add sub-item to last parent entry
- `tj + <item>` — Add item to current list
- `tj + @name <text>` — Append text to named entry
- `tj e` — New entry in $EDITOR
- `tj e [ai|do|p|b|j]` — New typed entry in $EDITOR
- `tj e <n>` — Edit entry in $EDITOR
- `tj e <n> -k` — Edit, keep original modification time
- `tj e <n> =ctx` — Set context (no editor)
- `tj e <n> <text>` — Replace content (with confirmation)
- `tj s <n>` or `tj s @name` — Show entry details
- `tj s =ctx` — Show context description
- `tj t <n> <tag>` — Add tag
- `tj t- <n> <tag>` — Remove tag
- `tj mv <n> <context>` — Move entry to context
- `tj ^ <n>` — Pin entry (update timestamp)
- `tj cp <n> <datetime>` — Copy journal entry to new date/time (also `tj cp <n> <date> <time>`)
- `tj archive <n>` — Archive entry
- `tj unarchive <n>` — Unarchive entry
- `tj d <n>` or `tj d @name` — Delete (with confirmation)
- `tj d <n1> <n2> ...` / `tj d <n1-n2>` — Delete multiple entries / range
- `tj d <n> t <tag>` / `tj d t <tag>` — Delete tag from entry / all instances of tag
- `tj d =context` — Delete context (if empty)

**Numbered shortcuts** — quick metadata on entry `<n>`:
- `tj <n> @name` / `tj <n> @0` — Set/clear name
- `tj <n> =context` / `tj <n> =0` — Set/clear context
- `tj <n> :tag` — Add tag
- `tj <n> p=N` / `tj <n> p=0` — Set/remove priority
- `tj <n> s=status` — Set status
- `tj <n> k=type` — Change kind (ai, b, do, j, m, p)
- `tj <n> l=N` / `tj <n> l=0` — Set/remove display truncation

## Calendar

- `tj c` — Next 30 days
- `tj c t` — Today only
- `tj c w` — This week
- `tj c m` — This month
- `tj c 60` — Next 60 days
- `tj c t+1` — Tomorrow
- `tj c w-1` — Last week
- `tj y` — Year summary with month headers

## Time Tracking

- `tj start [time]` — Start clock (e.g., `tj start 9am`)
- `tj stop [time|datetime]` — Stop clock; accepts a single date/time token, retroactive allowed (`5pm`, `yesterday`, `20260203/09:30`)
- `tj break <duration>` — Add break (e.g., `30` for minutes, `1h` for hours)
- Clock status shown in header when active

## Other

- `tj dump` — Entire database as executable tj commands (backup/restore)
- `tj config show` — Current configuration
- `tj tz [zone]` — Show or set timezone (eastern, central, pacific, euro, +/-N)
- `tj agent [=context] <prompt>` — Launch a guided Claude agent
- `tj admin backup` — Manual backup
- `tj admin purge` — Permanently delete soft-deleted entries
- `tj admin safe` / `tj admin normal` — Safe mode on/off (safe mode hides entries tagged `private`)
- `tj admin lines [N]` — Show/set content truncation line count
- `tj run <n|name>` — Execute action (from last `tj l actions` listing, or by name)
- `tj wake` — Send SIGHUP to action agent daemon
- `tj restart-agent` — Graceful agent restart (after current action completes)
- `tj h` — Help summary
