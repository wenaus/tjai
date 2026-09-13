# Cron Jobs

Scheduled jobs for the tjai ec2dev server. All run under the `admin` user crontab.

## Install

```bash
crontab /home/admin/github/tjai/scripts/cron/crontab
```

## Jobs

| Schedule | Script | Description |
|----------|--------|-------------|
| Every 30 min | `git_pull_repos.sh` | Pull every repo in `~/github`. swf-* repos auto-checkout highest `infra/baseline-vNN` branch |
| Every 5 min | `git_pull_repos.sh swf-only` | Pull swf-* repos only; this machine collaborates with live swf development and must not run 30-min-stale swf software |
| Every 10 min | `/var/www/corun-ai/scripts/sync_users.sh` | Sync user accounts from swf-remote to corun-ai (corun-ai script, scheduled from this crontab) |
| Daily 02:30 | `refresh_git_daily.py` | Heal empty `data/git_daily/<date>.md` files from prior days |
| Daily 02:40 | `refresh_git_weekly_loc.py` | Regenerate `data/git_weekly_loc.json`, the weekly lines-added-by-project data for the git page chart |
| Daily 03:00 | `../section_dev.py` | Generate dev activity data (upstream repo commits and PRs) |
| Daily 03:30 | `purge_old_versions.py` | Purge entry versions older than 30 days, keeping min 10 most recent per entry |
| Daily 03:45 | `prune_logs_and_digests.py` | Prune applog rows (info >7d, error >14d) and health-digest files (>7d) |
| Daily 04:00 | `cleanup_claude_sessions.sh` | Purge Claude Code session files (tool-results, debug, file-history) older than 2 days |

## Adding a new job

1. Create the script in this directory
2. Add it to the `crontab` file
3. Update this table
4. Install: `crontab /home/admin/github/tjai/scripts/cron/crontab`
