# Cron Jobs

Scheduled jobs for the tjai ec2dev server. All run under the `admin` user crontab.

## Install

```bash
crontab /home/admin/github/tjrepo/tjai/scripts/cron/crontab
```

## Jobs

| Schedule | Script | Description |
|----------|--------|-------------|
| Every 10 min | `git_pull_repos.sh` | Pull all tracked repos. swf-* repos auto-checkout highest `infra/baseline-vNN` branch |
| Daily 03:30 | `purge_old_versions.py` | Purge entry versions older than 30 days, keeping min 10 most recent per entry |
| Daily 04:00 | `cleanup_claude_sessions.sh` | Purge Claude Code session files (tool-results, debug, file-history) older than 2 days |

## Adding a new job

1. Create the script in this directory
2. Add it to the `crontab` file
3. Update this table
4. Install: `crontab /home/admin/github/tjrepo/tjai/scripts/cron/crontab`
