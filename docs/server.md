# Server & Deployment

## Production Environment

- **Server:** etaverse.com (AWS Debian), Apache mod_wsgi
- **Shell:** Bash only
- **Git repo (dev):** `/home/admin/github/tjrepo/tjai`
- **Production deployment:** `/var/www/tjai` (not a git repo)
- **Database:** PostgreSQL. `DJANGO_DATABASE_URL` is in `/var/www/tjai/.env` only.
- **Django settings:** `tjai_project/settings/base.py` (split settings dir)
- **Static files:** `tjai_app/static/tjai/` → served via Apache Alias at `/tjai/static/`
- **Action agent:** managed by supervisord (`deploy/supervisord.conf`), runs as `admin`

## Deploying

```bash
cd /home/admin/github/tjrepo/tjai
./deploy/update_from_dev.sh
```

This rsyncs code to `/var/www/tjai/` (excludes `data/`), installs requirements, runs migrations, collectstatic, and reloads Apache. Does NOT restart agents — see [action-agent.md](action-agent.md) for that.

## Django Commands (Production)

```bash
source /var/www/tjai/.venv/bin/activate
cd /var/www/tjai
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## Django Commands (Dev Tree)

The dev tree has no `.env`. Django needs `DJANGO_DATABASE_URL` from production's `.env` (which uses `KEY=VALUE` without `export`):

```bash
set -a && source /var/www/tjai/.env && set +a
cd /home/admin/github/tjrepo/tjai
.venv/bin/python manage.py makemigrations tjai_app --name <name>
.venv/bin/python manage.py migrate
```

## Web Endpoints

### Pages

| Path | Description |
|------|-------------|
| `/tjai/` | Dashboard — entry list, filtering by kind/context/tags/status |
| `/tjai/login/` | Authentication |
| `/tjai/entry/` | Entry detail with human-readable data display |
| `/tjai/daily/` | Daily synopsis (Today in History) |
| `/tjai/picks/` | AI-curated news picks triage |
| `/tjai/rss/` | RSS reader with source-grouped triage |
| `/tjai/readme/` | Reading list (items tagged :readme) |
| `/tjai/research/` | Research queue with agent status |
| `/tjai/research/studies/` | Subagent reports for a research topic |
| `/tjai/system/` | System health monitoring dashboard |
| `/tjai/agent-log/` | Action agent execution log |
| `/tjai/context/<name>/` | Browse entries in a context |
| `/tjai/tag/<name>/` | Browse entries with a tag |
| `/tjai/kind/<name>/` | Browse entries by type |
| `/tjai/mcp/` | MCP server for AI assistants |

### API

| Path | Auth | Description |
|------|------|-------------|
| `/tjai/api/health` | — | Health check |
| `/tjai/api/sync/push` | — | Push dirty entries from client |
| `/tjai/api/sync/pull` | — | Pull updates (paginated, 500/batch) |
| `/tjai/api/bulk-import` | Bearer | Bulk import bookmarks |
| `/tjai/api/add-bookmark` | Bearer | Single bookmark (Chrome extension) |
| `/tjai/api/add-journal` | Bearer | Journal entry (Gmail add-on) |
| `/tjai/api/dialog` | Bearer | Claude Code dialog turns GET/POST |
| `/tjai/api/command` | — | Server commands (sysconfig) |

## Application Logging

The action agent and subsystems log to stdout (supervisord) and the database (AppLog table).

- `DbLogHandler` (`tjai_app/db_log_handler.py`) — Python `logging.Handler` writing to AppLog model
- `action_runner` configures a logger with `DbLogHandler` (DB) + `StreamHandler` (stdout)
- **Web UI:** `/tjai/agent-log/` — filterable by level, auto-refreshes
- **API:** `/tjai/api/agent-log?limit=200&level=ERROR`
- **AppLog fields:** `source`, `timestamp`, `level`, `levelname`, `message`, `extra_data` (JSON)

**Files:** `tjai_app/db_log_handler.py`, `tjai_app/models.py` (AppLog), `tjai_app/views.py`, `tjai_app/templates/tjai_app/agent_log.html`

## System Health Monitoring

Real-time dashboard at `/tjai/system/` with auto-refresh.

**Monitors:** system (uptime, load, memory, disk), PostgreSQL (connections, cache hit, DB size), tjai (entry counts, agent status, action schedules with real-time tracking), backups (freshness, file presence, dump size), Dropbox (auto-restart if down), processes (Apache, CloudWatch, agents), CloudWatch (24h CPU, memory/swap/disk trends).

**Health banner:** Green (normal), Yellow (load > 2x CPUs, memory < 20%, disk > 80%, backup > 1 day), Red (load > 3x CPUs, memory < 10%, disk > 90%, agents down, no backups).

`system_health.py` writes to SysConfig (`system_health_data` JSON, `system_health_status` color). Agent execution tracked via SysConfig keys, written by `action_runner.py` and `agent_complete.py`.

**Files:** `scripts/system_health.py`, `scripts/agent_complete.py`, `tjai_app/templates/tjai_app/system_health.html`, `tjai_app/views.py`

## Server Backups

Automated daily backup to Dropbox (`~/Dropbox/tjai-backups/server/YYYY-MM-DD/`).

| Item | Filename | Source |
|------|----------|--------|
| PostgreSQL | `tjai-db.sql.gz` | `pg_dump`, gzip |
| Production secrets | `env-www.env` | `/var/www/tjai/.env` |
| Personal API keys | `env-home.env` | `~/.env` |
| Data files | `data/` | `/var/www/tjai/data/` |
| Apache config | `etaverse.conf` | `/etc/apache2/sites-enabled/etaverse.conf` |

Runs overnight as a tjai action (`trigger=overnight`, `interval_hours=24`). Health page monitors freshness and completeness.

**File:** `scripts/backup.py`
