# Git Activity Page

`/tjai/git/` shows commit activity across every followed repository: a
contributions grid from 2026-02-01 onward, monthly totals with a top-five-day
leaderboard, a weekly lines-added chart by project with its share pie, and
reverse-chronological per-day commit lists linking each commit to GitHub.

## Repository registry

`_GIT_REPOS` in `tjai_app/views.py` is the single registry of followed
repositories (local path, GitHub URL, label). `scripts/section_git.py` (the
daily-synopsis Git section) and `scripts/cron/refresh_git_daily.py` import
it; adding a repository is one row. Local checkouts are kept current by
`scripts/cron/git_pull_repos.sh` (system cron: every 30 minutes for all
repositories, every 5 minutes for `swf-*`).

TJAI and tjlinks use standalone checkouts. `GIT_IMPORTED_HEADS` records the final
imported commit in each; every producer excludes that commit and its ancestors
with `git_history_exclusions`. Their earlier activity remains recorded through
tjrepo, and subsequent commits link to the public repositories. The weekly chart
attributes both repositories to the tjai project without counting the imported
history a second time. TeamComms AI is followed as `wenaus/teamcomms-ai`;
its weekly lines contribute to the AI category.

## Data flow

- **Daily files** — `data/git_daily/<YYYY-MM-DD>.md`, one per Eastern day:
  commits from `git log --all` by `GIT_AUTHOR` (beside `_GIT_REPOS`, the one
  definition the weekly chart uses too) bounded by that day's ET midnights,
  grouped by repository, with tjrepo commits attributed to a top-level directory via
  `diff-tree`. Each commit renders as a GitHub-linked line plus the first
  body line; `Co-Authored-By` trailers are dropped.
- **Producers** — a page load regenerates today's and yesterday's files from
  live git state (`_refresh_recent_git_daily`), so the page is current the
  moment it is viewed; `scripts/cron/refresh_git_daily.py` (system cron
  03:00 ET) rewrites the last three days, covering an in-flight day, a
  late-midnight commit, and a push that arrived after its day ended. Nothing
  older is touched: the checkouts are pulled every 30 minutes and the script
  fetches before it reads, so a day's file is complete once the day is over.
  `refresh_git_daily.py --days N` rewrites further back, which is how a change
  in what counts as a commit of his reaches the days already stored.
- **Page data** — `git_activity_data` reads the files: the grid's heatmap
  counts commit lines per file, and the newest 120 days are regrouped to
  app-level headers (`_restructure_git_md` maps tjrepo commit-message
  prefixes such as `tjai:` to their app) and rendered to HTML. Refresh
  errors are carried in the payload and surfaced on the page.
- **Weekly lines-added** — `scripts/cron/refresh_git_weekly_loc.py` (system
  cron 03:10 ET) writes `data/git_weekly_loc.json`: lines added (`numstat`) in
  the configured author's commits, bucketed by ISO week in the tjai
  timezone, attributed to a project by repository — for tjrepo, by top-level
  directory, and for etaverse by subject (below). Every repository under
  `/home/admin/github` is read; git
  worktrees (a `.git` file, not a directory) are skipped, since a worktree
  of a followed repository would count its history a second time.
  `git_weekly_loc_data` serves the file; the stacked-bar chart below the
  grid and the share pie under it render it.

### What the weekly lines count

The metric is gross lines added: every added line in a counted file, with
in-repository renames detected (`-M`) so a move counts near zero. It is not
net source growth; deleted lines are not subtracted. Excluded:

- **Generated and vendored build output** by path fragment (`node_modules/`,
  `dist/`, `.venv/`, …) and suffix (`.min.js`, `package-lock.json`, `.lock`,
  `.dae`, `.bvh`, `.dbml`); snapshot copies of own code (`-old.`, `_broken.`,
  `SUPERSEDED`).
- **Generated output and imported material**, by explicit tjrepo path
  (`DATA_PATHS`): the imported recipe collection and Yum XML exports, synced third-party skills,
  meadow route geometry, Blender
  factory build output, recorded production evidence, rail survey reports,
  image-generation output, exported Second Life shape data, generated
  IAU/JPL catalogs and orbital states, imported Second Life XML,
  the vendored LSL optimizer and generated change inventories.
- **Generated/imported Earth publication bundles**, by exact output path under
  `etaverse/assets/earth/curation/`: Mappeteer recovery and reviewed region geometry, Appalachia boundaries,
  normalized Berkshires bookmarks, Love's, Texas Roadhouse, national parks,
  Pathways and its scenic-route expansion, mountain regions, science labs, great universities, great museums
  and historic sites, character lodging, Much loved bookstores, and the `cultural-highlights/*.json`, `camping/*.json`
  and `hiking-cycling/*.json` bundles. The adjacent authored
  `*-selection.json` inputs and the builder/recovery/publisher code count.
- **Generated family JSON** identified from the registered generators in
  `etaverse/scripts/families/__init__.py`. The counter reads generator source
  without executing it. The Python generators count; their expanded records
  under `world/families/` do not. Handwritten family JSON remains counted.
- **Archives and staging directories** in tjrepo (`EXCLUDE_TJREPO_TOP`), the
  enumerated cross-repository moves.

Authored material counts in every format: family generators, handwritten
component definitions, houses,
finishes, Blender presets and recipes, curated catalogs, avatar libraries,
celestial layouts, biome and site definitions, application code, tests and
documentation. JSON and commit size are not reasons for exclusion.
Project `package.json` and `tsconfig.json` files count; copies inside excluded
third-party packages or generated output do not. New generated or imported
locations are added to `DATA_PATHS`; the next run recomputes every week from
git history, so the whole chart follows the rule.

### Etaverse by subject

Etaverse counts as six projects, shades of the virtual-world family's orange:
`etaverse-core` (the server and federation), `etaverse-client` (scene,
rendering, terrain, sky, physics, camera, navigation, the control window and
the V1 client), `etaverse-building` (houses, structures, furniture, families,
finishes, smart objects, editing and the built places), `etaverse-avatars`
(bodies, hair, attire, outfits, animations, poses, seat fitting),
`etaverse-vehicles` (ships, trains, boats, flight, docking) and `etaverse-rl`
(the Earth atlas, the catalogue stars, astronomy and the voyage). Etaverse's
directories divide by layer rather than subject, so `ETAVERSE_SUBJECTS` reads
the subject from the file's path: ordered name patterns, the first match
deciding, an unmatched file counting as client. A file serving several
subjects counts where its name puts it; the world scene, the largest client
file, also carries edits made for Earth. A new module whose name carries none
of its subject's words is added to its pattern, and the next run recomputes
every week.

### Palette

Each pair of neighbours in the stacking order, and the pie's last slice
against its first, is compared by `scripts/check_git_chart_palette.py`:
CIEDE2000 difference under normal vision and simulated protanopia,
deuteranopia and tritanopia, and each colour against the page's dark surface.
A change of palette or order is run through it (`uv run
scripts/check_git_chart_palette.py`, which supplies its one dependency).

## Dates

All page dates are Eastern, tjai's canonical timezone. The grid's current
day is computed in `America/New_York` (an `Intl` formatter), never from the
viewer's clock — a viewer west of Eastern sees the new ET day's square
before their own midnight. Client-side date arithmetic carries dates at
local noon so day steps cross DST changes safely, and no date passes through
a UTC round trip.

## Interaction

Grid cells and leaderboard entries carry their date; clicking one scrolls to
and highlights that day's commit list. Day headers show a commit-count
badge. The page and its data endpoint are served with `no-store`, so a
reload always reflects the current git state.

## Files

- `tjai_app/views.py` — `git_activity` (page), `git_activity_data`,
  `git_weekly_loc_data`, `_refresh_recent_git_daily`, `_restructure_git_md`,
  `_GIT_REPOS`
- `tjai_app/templates/tjai_app/git_activity.html` — grid, stats, chart, and
  day-list rendering
- `scripts/cron/refresh_git_daily.py`, `scripts/cron/refresh_git_weekly_loc.py`,
  `scripts/cron/git_pull_repos.sh` — producers (system cron; see
  `scripts/cron/crontab`)
- `scripts/check_git_chart_palette.py` — the palette's neighbour check
- `data/git_daily/*.md`, `data/git_weekly_loc.json` — the page's data
