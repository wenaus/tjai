# Next Steps

## Immediate: Deploy web dashboard

Dashboard added but not yet tested. Files:
- `tjai_app/templates/tjai_app/dashboard.html` - HTML/JS with 10-second auto-refresh
- `tjai_app/views.py` - Added `dashboard()`, `dashboard_calendar()`, `dashboard_status()`
- `tjai_project/urls.py` - Routes: `/`, `/api/dashboard/calendar`, `/api/dashboard/status`

To deploy:
1. Pull latest on server
2. Restart Django (gunicorn/uwsgi)
3. Visit root URL `/`

Dashboard features:
- Two-panel layout: calendar (left), status (right)
- Calendar: next 30 days of journal entries, grouped by week/day
- Status: timestamp, clock status (if active), recent entries
- Dark theme, monospace font matching terminal
- Clock entries colored green (start=light, stop=darker)
- Links are clickable

## Pending

1. **Fix migration warning** - `manage.py makemigrations` reports model changes not reflected in migrations. Investigate and resolve.

2. **Move backup to agent** - backup only when dirty=0 and recently synced, removes latency from tj commands

3. **MCP server in agent** - expose tj to Claude Code

## Under consideration: Obsidian integration

Complement tj's quick captures with Obsidian's rich markdown documents.
