# Agents using the action agent system

## Daily Synopsis

A journal entry (`daily-{YYYY-MM-DD}`) built overnight from independent section modules that each append a `## Heading` block.

### Pipeline

Three action entries run in sequence by `scheduled_time`:

| Time | Action | Type | What it does |
|------|--------|------|-------------|
| 00:10 | `daily-synopsis` | Mechanical | Creates entry, runs all section modules |
| 00:15 | `daily-history` | Mechanical + AI | Fetches Wikipedia "on this day", AI curates |
| 01:00 | `daily-assessment` | AI only | Health assessment + dialog summary |

The `daily-synopsis` action creates the journal entry, then runs its `mechanical_script` list:

```
["section_keeps.py", "section_git.py", "section_backup.py", "health_digest.py", "section_health.py"]
```

Each script appends its `## Section`. Ordering matches the list order.

### Section Module Framework

All section scripts live in `scripts/` and use `synopsis_utils.py`:

```python
#!/usr/bin/env python3
"""Appends ## MySection to the daily synopsis entry."""
import bootstrap  # noqa: F401
from synopsis_utils import main_section

def build(since_ts, target_date):
    """Return markdown body string, or None to skip."""
    # since_ts = time.time() - 86400 (24h lookback)
    # target_date = date object for the synopsis day
    return 'Some markdown content'

if __name__ == '__main__':
    main_section('MySection', build)
```

`main_section()` handles argument parsing and entry lookup. `append_section()` is idempotent — first run appends, re-runs replace in place.

### Adding a New Section

1. Create `scripts/section_foo.py` following the pattern above
2. Add `section_foo.py` to the `daily-synopsis` action's `mechanical_script` list at the desired position
3. Deploy with `deploy/update_from_dev.sh`

No agent restart needed — section scripts run as subprocesses.

### Existing Sections

| Script | Heading | Data Source |
|--------|---------|-------------|
| `section_keeps.py` | Keeps | DB: saved bookmarks + kept picks, last 24h |
| `section_git.py` | Git | `git log --since` on `/home/admin/github/tjrepo` |
| `section_backup.py` | Backups | `~/Dropbox/tjai-backups/server/` directory scan, 7-day table |
| `section_health.py` | System Health | `data/health-digest/{date}.json` (written by `health_digest.py`) |

### Web UI

- `/tjai/daily/` — date list + rendered markdown content
- Rerun button triggers `daily_history_rerun_date` sysconfig, wakes agent
- Markdown rendered with extensions: `tables`, `fenced_code`, `nl2br` (tab_length=2)

---

## AI Picks (Curated News)

An AI-driven news curation system that researches tech/science/culture sources overnight and presents a triage page for quick review.

### How It Works

1. An AI agent researches configured sources (The Register, Ars Technica, HN, Nature, CERN, ArXiv, NVIDIA, AWS, GitHub, Reddit, etc.)
2. Creates ~30 bookmark entries per run in the `picks` context, each with precis and rationale
3. The Picks page (`/tjai/picks/`) presents them grouped by run for triage

**Sources:** Defined in a tjai entry (`picks-sources`, editable via the Sources link on the Picks page). The agent reads this before each run.

### Picks Page (`/tjai/picks/`)

- Picks grouped by run, reverse chronological
- Each pick: title (link to article, opens new tab), source, precis, rationale
- **Click title** — opens article, marks viewed/archived (goes grey)
- **Thumbs up/down** — training signal in `data.thumbs`
- **Keep** — lasting value (green accent), protected from Archive All
- **ReadMe** — adds `:readme` tag, appears on ReadMe page
- **Archive All** (per run) — archives all non-kept items
- Stats bar: total/to-review/kept/archived

### ReadMe Page (`/tjai/readme/`)

Reading list of items tagged `:readme`. Title, date, URL, tags, precis. Click opens article and removes tag. Remove button for manual removal.

### Data Model

```python
Entry(
    kind='bookmark', context='picks',
    content='[Article Title](https://example.com/article)',
    data={
        'run': '2026-02-23T02:00:00',
        'precis': 'Summary...',
        'rationale': 'Why this is relevant...',
        'source': 'theregister.com',
        'thumbs': None,     # null | 'up' | 'down'
        'kept': False, 'archived': False, 'readme': False,
    }
)
```

### UI Behavior

- Sticky run-date headers while scrolling
- Fully processed runs with no kept items disappear
- Fully processed runs with kept items show only kept (archived removed)
- Archive All at top and bottom of each run

### Files

- `tjai_app/views.py` — `picks`, `api_picks_data`, `api_picks_update`, `api_picks_archive_run`, `readme_page`, `api_readme_data`, `api_readme_dismiss`
- `tjai_app/templates/tjai_app/picks.html`, `readme.html`

---

## Research Queue

"Computer, perform an analysis." Deep autonomous research — the system dispatches **three models in parallel** (Claude, Gemini, and a local-hardware Gemma running on Torre's Mac Studio via the [Remote Worker Pipeline](remote-workers.md)). Each model produces an independent analyst's brief, then a final synthesis pass merges them into a single report.

### How It Works

1. Create a memory entry tagged `:research_topic` with topic description
2. Research page (`/tjai/research/`) shows the queue with status
3. Click **Submit** (or **Submit All**). The research-agent action runs `_dispatch_research_3way` (`tjai_app/action_runner.py`), which dispatches each model in `RESEARCH_MODELS = ('claude','gemini','gemma')` via its own mechanism:
   - **Claude** — detached Claude instance with research-optimized system prompt, spawning parallel subagents that search the web and write findings tagged `research-subagent`
   - **Gemini** — `scripts/research_multimodel.py gemini` subprocess via the Gemini API
   - **Gemma** — staged for the [remote worker pipeline](remote-workers.md): the prompt is written to a sub-entry with `worker_target='gemma4'`, the local research-agent then exits, and `tj_agent` running on the Mac Studio long-polls `/api/worker/poll`, claims the work, runs `gemma3` via ollama, and POSTs the result back
4. As each model finishes, `research_model_complete` updates the base entry's `{model}_status`. When **all dispatched models** are `done`, the base entry transitions to `done` and synthesis is dispatched
5. **Synthesis** — Claude is dispatched again with the synthesis prompt and links to all per-model reports, producing the final merged analyst's brief
6. Automatically chains to next pending item (priority order, then FIFO)

### Research Page (`/tjai/research/`)

The page banner displays **three named, non-overlapping facts** so nothing reads as contradictory (this rule was learned the hard way — see `docs/remote-workers.md` § Display contract):

1. **Local agent** — the research-agent process state (`idle`/`running`/`failed`). Idle while remote workers are doing inference is correct and intentional.
2. **Remote workers** — one line per known capability (e.g. `gemma4`) with derived state `busy`/`idle`/`disconnected`/`zombie`/`unknown`. State is derived from the combination of last poll and held claims, not just poll age — a worker doing a 5-minute inference is `busy`, not `disconnected`.
3. **N topics in flight** — per-base-entry breakdown with per-model phases (`claude done · gemini done · gemma in-progress on ed8e0e3a (49s ago)`).

The activity dot reflects the system as a whole: green = something is actively working anywhere, red = a worker is disconnected or holding a zombie claim, orange = work staged waiting for a worker, grey = idle.

- **Submit** / **Submit All** — trigger research
- **Rerun selections** — selectively rerun specific models on a topic (e.g., just gemini)
- **Stop** — soft stop (finish current, don't chain)
- **Abort** — hard stop (kill local agent)
- **Studies** link per item — subagent reports (Claude only)
- Agent log link for debugging

### Studies Page (`/tjai/research/studies/`)

All subagent entries for a topic. Linked via `data.source_uuid`.

### Quality Controls

System prompt (`research-system-prompt` entry) enforces:
- Depth over breadth
- Primary sources first (papers, docs, repos)
- Cross-referencing with contradiction tracking
- Epistemic honesty (fact vs. consensus vs. debate vs. speculation)
- All content in tjai entries (no ephemeral files)

### Data Models

**Base research entry** (the topic):
```python
Entry(kind='memory', status='pending',  # pending → active → done
    content='Topic title\n\nDescription...',
    data={
        'entry_id': 'research-mcp',
        'started_at': 1771962258.67,
        # Per-model tracking, populated by _dispatch_research_3way
        'claude_status': 'done',         # None|staged|active|done|blocked|rerun
        'gemini_status': 'done',
        'gemma_status':  'active',
        'claude_entry_id': 'research-mcp-claude',
        'gemini_entry_id': 'research-mcp-gemini',
        'gemma_entry_id':  'research-mcp-gemma',
        'synthesis_triggered': False,    # set True once all models done
    })
# Tags: 'research_topic'
```

**Per-model sub-entry** (one per dispatched model):
```python
Entry(kind='memory', status='active',     # active → done|blocked
    content='Topic + result',
    data={
        'entry_id': 'research-mcp-gemma',
        'source': 'multimodel',
        'base_entry_id': 'research-mcp',
        'model': 'gemma',
        # Remote-worker fields (gemma only, cleared on completion)
        'worker_target': 'gemma4',
        'worker_prompt': '...',
        'worker_staged_at': 1771962258.67,
        'worker_claimed_at': 1771962261.42,
        'worker_claimed_by': 'ed8e0e3a-...',
    })
# Tags: 'research_topic'
```

**Study/subagent entry** (Claude-only — its parallel subagents):
```python
Entry(kind='memory',
    content='<task-notification>...findings...</task-notification>',
    data={'entry_id': 'research-mcp:mcp-server-ecosystem-survey',
          'source_uuid': 'parent-uuid', 'source_entry_id': 'research-mcp'})
# Tags: 'research-subagent', 'ccdialog'
```

### Infrastructure

Uses the Action Agent pipeline. The `research-agent` action entry defines the Claude prompt template, model (Opus), timeout (2h), and references the system prompt entry. Lifecycle via SysConfig keys, monitored by watchdog, finalized by `agent_complete.py`.

The remote-worker side has its own protocol — see [remote-workers.md](remote-workers.md) for the long-poll endpoint, capability whitelist, claim lifecycle, free-capacity reset, stale-claim auto-reclaim, and display contract.

### Files

- `tjai_app/views.py` — `research_page`, `api_research_data/run/stop/abort`, `research_studies`, `api_research_studies`, `worker_poll`, `worker_result`, `_claim_worker_entry`
- `tjai_app/action_runner.py` — `_dispatch_research_3way` (per-model dispatch), `research_model_complete` (per-model completion + synthesis trigger), `_create_and_dispatch_synthesis`
- `tjai_app/templates/tjai_app/research.html`, `research_studies.html`
- `scripts/research_multimodel.py` — Gemini subprocess dispatcher
- `scripts/agent_complete.py` — Claude post-completion + queue drain
- `tj_agent/worker.py` — Remote worker loop (Mac side)

---

## RSS Reader

In-app RSS feed reader with source-grouped triage UI.

### How It Works

1. Sources defined in a tjai entry (`rss-sources`, editable on RSS page) — one URL per line, grouped by category headers (`## tech`, `## science`, etc.)
2. `fetch_rss.py` fetches feeds via `feedparser`, deduplicates by URL, creates `RssItem` records
3. RSS page (`/tjai/rss/`) presents unread items grouped by category and source

### RSS Page (`/tjai/rss/`)

- Items grouped by category, then source feed
- Sticky category and source headers
- Each item: title (link), published date, precis
- **Click title** — opens article, marks read (goes grey)
- **ReadMe** — adds to reading list
- **▲ Clear** — marks this and all above as read
- **Mark Read** (per source) — with green toast confirmation
- **Mark All Read** (global)
- **Fetch Now** — immediate feed fetch
- **+ Add Source** — modal for new feed URL with category

### Data Model

Uses `RssItem` Django model (not tjai entries):

```python
RssItem(guid, url, title, source, category, published, precis, is_read)
```

### Files

- `scripts/fetch_rss.py` — Fetcher using `feedparser`
- `tjai_app/models.py` — `RssItem` model
- `tjai_app/views.py` — `rss_page`, `api_rss_*` endpoints
- `tjai_app/templates/tjai_app/rss.html`
