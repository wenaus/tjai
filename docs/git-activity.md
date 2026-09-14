# Git Activity Page

`/tjai/git/` shows commit activity across every followed repository: a
contributions grid from 2026-02-01 onward, monthly totals with a top-five-day
leaderboard, a weekly lines-added chart by project, and reverse-chronological
per-day commit lists linking each commit to GitHub.

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
  02:30) rewrites the last three days, covering an in-flight day, a
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
  cron 02:40) writes `data/git_weekly_loc.json`: lines added (`numstat`) in
  the configured author's commits, bucketed by ISO week in the tjai
  timezone, attributed to a project by repository — for tjrepo, by top-level
  directory. Every repository under `/home/admin/github` is read; git
  worktrees (a `.git` file, not a directory) are skipped, since a worktree
  of a followed repository would count its history a second time.
  `git_weekly_loc_data` serves the file; the stacked-bar chart below the
  grid renders it.

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
- `data/git_daily/*.md`, `data/git_weekly_loc.json` — the page's data
