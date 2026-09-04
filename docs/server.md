# Server & Deployment

## Production Environment

- **Server:** etaverse.com (AWS Debian), gunicorn behind Apache reverse proxy
- **Shell:** Bash only
- **Git repo (dev):** `/home/admin/github/tjrepo/tjai`
- **Production deployment:** `/var/www/tjai` (not a git repo)
- **Database:** PostgreSQL 15. `DJANGO_DATABASE_URL` is in `/var/www/tjai/.env` only. Server config: `shared_buffers = 1GB` and `shared_preload_libraries = 'pg_stat_statements'` (set 2026-07-19 in `/etc/postgresql/15/main/postgresql.conf`; the 128MB Debian default made every seq scan of `entries` bypass the buffer cache).
- **Django settings:** `tjai_project/settings/base.py` (split settings dir)
- **Static files:** `tjai_app/static/tjai/` → served via Apache Alias at `/tjai/static/`
- **Python:** version requested in `.python-version` (series floor), provisioned by uv; see [Python Environment](python-environment.md)
- **Web server:** gunicorn on port 8002, managed by systemd (`deploy/tjai-gunicorn.service`), runs as `www-data`
- **MCP server:** standalone FastMCP/ASGI service on port 8003, managed by systemd (`deploy/tjai-mcp-asgi.service`), runs as `www-data`
- **Action agent:** managed by supervisord (`deploy/supervisord.conf`), runs as `admin`; supervisord itself runs under systemd (`deploy/tjai-supervisord.service`)
- **Gunicorn py-spy watchdog:** systemd service (`deploy/tjai-gunicorn-pyspy-watchdog.service`, `scripts/gunicorn_pyspy_watchdog.py`)

MCP operations are documented in `docs/mcp.md`.

## Deploying

```bash
cd /home/admin/github/tjrepo/tjai
./deploy/update_from_dev.sh
```

This rsyncs code to `/var/www/tjai/` (excludes `data/`), installs requirements, runs migrations, collectstatic, reloads gunicorn, restarts the MCP ASGI and Telegram bot services, and schedules a graceful action-agent restart (the agent finishes any in-progress action, exits, and supervisord restarts it on the new code).

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
| `/tjai/` | Public landing page |
| `/tjai/login/` | Authentication |
| `/tjai/dashboard/` | Dashboard — entry list, filtering by kind/context/tags/status; see [Dashboard](dashboard.md) |
| `/tjai/entry/` | Entry detail with human-readable data display |
| `/tjai/diary/` | Diary page |
| `/tjai/inflight/`, `/tjai/inflight/<entry_id>/` | Inflight todos: frame content of Capcom's inflight view (`?embed=1`); bare visits redirect to `/tjai/capcom/?view=inflight`; see [Inflight Todos](inflight.md) |
| `/tjai/versions/` | Recent entry changes across all entries; see [Entry Versions](versions.md) |
| `/tjai/captures/` | Images stashed from mail by the Gmail add-on, with delete; see [Add-ons](addons.md#stash-images-captures) |
| `/tjai/synopsis/` | Daily synopsis (Today in History) |
| `/tjai/this-week/` | Current in-progress workweek (Sat–Fri), one row per day |
| `/tjai/weekly/` | Index of past workweek entries (reverse chronological) |
| `/tjai/workday/<yyyymmdd>/` | Workday entry get-or-create; redirects to entry detail |
| `/tjai/workweek/<yyyymmdd>/` | Single workweek entry — topical summary assembled from Sat–Fri workday entries |
| `/tjai/assessment/` | AI assessment reports |
| `/tjai/goals/` | Goals browser |
| `/tjai/git/` | Git activity |
| `/tjai/dev/` | Development activity |
| `/tjai/picks/` | AI-curated news picks triage |
| `/tjai/rss/` | RSS reader with source-grouped triage |
| `/tjai/readme/` | Reading list (items tagged :readme) |
| `/tjai/research/` | Research queue with agent status |
| `/tjai/research/studies/` | Subagent reports for a research topic |
| `/tjai/research-list/` | Research list |
| `/tjai/research-detail/` | Research detail |
| `/tjai/system/` | System health monitoring dashboard |
| `/tjai/agent-log/` | Action agent execution log |
| `/tjai/agent-queue/` | Action agent queue |
| `/tjai/context/<name>/` | Browse entries in a context |
| `/tjai/tag/<name>/` | Browse entries with a tag |
| `/tjai/kind/<name>/` | Browse entries by type |
| `/tjai/poetry/author/<name>/` | Entries by poetry author |
| `/tjai/relate/<uuid>/` | Relate-to picker, opened from the entry edit panel |
| `/tjai/p/<entry>/`, `/tjai/p/context/<name>/` | Public entry and context pages (no auth) |
| `/tjai/m/` | Telegram Mini App |
| `/tjai/mcp/` | MCP server for AI assistants, proxied to standalone ASGI service |

### API

| Path | Auth | Description |
|------|------|-------------|
| `/tjai/api/health` | — | Health check |
| `/tjai/api/sync/push` | REST bearer | Push dirty entries from client |
| `/tjai/api/sync/pull` | REST bearer | Pull updates (paginated, 500/batch) |
| `/tjai/api/worker/poll` | REST bearer | Claim remote-worker jobs |
| `/tjai/api/worker/result` | REST bearer | Complete remote-worker jobs |
| `/tjai/api/work/*` | REST bearer | Submit, inspect, and dispose of external work |
| `/tjai/api/bulk-import` | Bearer | Bulk import bookmarks |
| `/tjai/api/add-bookmark` | Bearer | Single bookmark (Chrome extension) |
| `/tjai/api/add-journal` | Bearer | Journal entry (Gmail add-on) |
| `/tjai/api/add-entry` | Bearer | Generic entry from external sources |
| `/tjai/api/add-capture` | Bearer | Multipart image stash from the Gmail add-on |
| `/tjai/capture/<uuid>/<file>` | Session or bearer | One stashed image |
| `/tjai/api/capture/<uuid>/delete` | Session | Delete a capture: files and entry |
| `/tjai/api/capture/<uuid>/file/<file>/delete` | Session | Delete one image of a capture |
| `/tjai/api/log` | Bearer | Write to AppLog from external sources |
| `/tjai/api/kozy-chat` | Bearer | KozyKorner persistent chat |
| `/tjai/api/dialog` | Bearer | Claude Code dialog turns GET/POST |
| `/tjai/api/dialog/daily-counts` | Session | Daily dialog turn counts |
| `/tjai/api/entry/create` | Session | Create entry, returns UUID |
| `/tjai/api/inflight/list`, `/tjai/api/inflight/<id>/state`, `/tjai/api/inflight/<id>/item` | Session | Inflight todos: list, polled state, item actions (done, reopen, add) |
| `/tjai/api/tg-auth` | Telegram initData | Create session for the Telegram Mini App |
| `/tjai/api/command` | REST bearer or session | Server commands (sysconfig) |

The REST bearer is `TJAI_API_KEY`, whose server-side value is stored in
`SysConfig.gmail_addon_api_key`. Direct, unproxied loopback calls are trusted so
same-host services can use the control plane without routing a secret through
their local process configuration. Apache-proxied requests are never treated as
loopback because they carry `X-Forwarded-For`.

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

**Monitors:** system (uptime, load, memory, disk), PostgreSQL (connections, cache hit, DB size — cache hit is computed over the trailing 24h of counter samples stored in SysConfig `pg_cache_hit_samples`, not the cumulative `pg_stat_database` ratio, so it recovers promptly after a postgres restart's cold reads), tjai (entry counts, agent status, action schedules with real-time tracking), TJAI-launched Codex subscription usage (24h/7d tokens, per-action averages, timeouts, and missing reports), backups (freshness, file presence, dump size), web apps (epic-devcloud endpoint probes), processes (Apache, CloudWatch, agents), CloudWatch (24h CPU, memory/swap/disk trends).

Codex accounting is process-level and forward-looking. Each TJAI-launched subscription Codex process writes one `source=llm_usage`, `event=llm_usage` AppLog row. A missing Codex token footer is recorded as `usage_reported=false`, not zero. Interactive Codex sessions and API-billed providers are outside this accounting block.

**Health banner:** Green (normal), Yellow (load > 2x CPUs, memory < 20%, disk > 80%, swap > 20%, backup > 1 day), Red (load > 3x CPUs, memory < 10%, disk > 90%, swap > 50%, agents down, no backups).

`system_health.py` writes to SysConfig (`system_health_data` JSON, `system_health_status` color). Agent execution tracked via SysConfig keys, written by `action_runner.py` and `agent_complete.py`.

**Files:** `scripts/system_health.py`, `scripts/agent_complete.py`, `tjai_app/templates/tjai_app/system_health.html`, `tjai_app/views.py`

## Server Backups

Automated daily backup to `~/tjai-backups/server/YYYY-MM-DD/`, pushed to Dropbox via rclone (`dropbox:tjai-backups/server`).

| Item | Filename | Source |
|------|----------|--------|
| PostgreSQL | `tjai-db.sql.gz`, `corun-db.sql.gz`, `swf-remote-db.sql.gz`, `etaverse-db.sql.gz`, `primus-db.sql.gz`, `pax-eden-db.sql.gz` | `pg_dump`, gzip |
| Production secrets | `env-www.env` | `/var/www/tjai/.env` |
| Personal API keys | `env-home.env` | `~/.env` |
| App env files | `env-corun.env`, `env-swf-remote.env`, `env-etaverse.env`, `env-primus.env`, `env-pax-eden.env` | Production env files used to read database credentials |
| Data files | `data/` | `/var/www/tjai/data/` |
| Apache config | `etaverse.conf` | `/etc/apache2/sites-enabled/etaverse.conf` |

Runs overnight as a tjai action (`trigger=overnight`, `interval_hours=24`). Health page monitors freshness and completeness. Backup runs around 09:00 UTC — changes after that are not covered until the next day.

**File:** `scripts/backup.py`

### Restoration

**Full database restore** (nuclear option — replaces everything):

```bash
gunzip -c ~/tjai-backups/server/YYYY-MM-DD/tjai-db.sql.gz > /tmp/restore.sql
# Drop and recreate:
sudo -u postgres dropdb tjai
sudo -u postgres createdb tjai
sudo -u postgres psql tjai < /tmp/restore.sql
```

**Selective restore** (recover specific rows from a table):

1. Extract the dump:
   ```bash
   gunzip -c ~/tjai-backups/server/YYYY-MM-DD/tjai-db.sql.gz > /tmp/restore.sql
   ```

2. Find the COPY block for the table:
   ```bash
   grep -n 'COPY public.tablename' /tmp/restore.sql
   ```

3. Extract specific rows. The COPY format is tab-separated. Example — extract `system_health` rows from `applog` (source is column 2):
   ```bash
   # Extract COPY header
   awk '/^COPY public.applog/,/^\\\\.$/' /tmp/restore.sql | head -1 > /tmp/partial.sql
   # Extract matching rows
   awk -F'\t' '/^COPY public.applog/,/^\\\\.$/{if($2=="system_health") print}' /tmp/restore.sql >> /tmp/partial.sql
   # Add terminator
   echo '\\.' >> /tmp/partial.sql
   ```

4. Re-insert using Python (handles ID conflicts):
   ```python
   import psycopg, os
   conn = psycopg.connect(os.environ['DJANGO_DATABASE_URL'])
   cur = conn.cursor()
   with open('/tmp/partial.sql') as f:
       for line in f.readlines()[1:-1]:  # skip COPY header and terminator
           parts = line.strip().split('\t')
           cur.execute('INSERT INTO applog (...) VALUES (%s,...) ON CONFLICT (id) DO NOTHING', parts)
   conn.commit()
   ```

   Use `ON CONFLICT (id) DO NOTHING` to skip rows that already exist. Always validate with `conn.rollback()` before committing.

**Restoring other items:**

- **Secrets:** Copy `env-www.env` → `/var/www/tjai/.env`, `env-home.env` → `~/.env`
- **Data files:** Copy `data/` → `/var/www/tjai/data/`
- **Apache config:** Copy `etaverse.conf` → `/etc/apache2/sites-enabled/`, then `sudo systemctl reload apache2`

### Applog cleanup

The action agent automatically prunes `action_agent` and `system_health` log entries older than 7 days (runs hourly in the main loop). Other sources, e.g. `health_digest` and `llm_usage`, are preserved indefinitely.
