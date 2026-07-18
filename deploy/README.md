# tjai Deployment

Production runs from `/var/www/tjai` on etaverse.com.

The web app and MCP endpoint are separate services:

- `tjai-gunicorn.service`: Django web/API app on `127.0.0.1:8002`
- `tjai-mcp-asgi.service`: FastMCP ASGI endpoint on `127.0.0.1:8003`

## Shared Infrastructure

tjai shares server infrastructure with primus. The following are in `primus/deploy/`:

- `install_system_deps.sh` - Install Apache, PostgreSQL, Python, certbot
- `setup_postgres.sh` - Creates both primus and tjai databases
- `configure_apache.sh` - Configures Apache for both apps
- `etaverse.conf` - Apache config serving both /primus and /tjai

## Fresh Server Setup

1. Run shared setup from primus/deploy:
   ```bash
   cd ~/github/tjrepo/primus/deploy
   sudo ./install_system_deps.sh
   ./setup_postgres.sh
   ./configure_apache.sh --ssl
   ```

2. Deploy tjai:
   ```bash
   cd ~/github/tjrepo/tjai/deploy

   # Create .env (get DB password from setup_postgres.sh output)
   cp ../.env.example /var/www/tjai/.env
   # Edit /var/www/tjai/.env with correct DJANGO_DATABASE_URL

   # Deploy
   ./update_from_dev.sh

   # Install service files when provisioning a new host
   sudo cp tjai-gunicorn.service tjai-mcp-asgi.service tjai-tgbot.service tjai-supervisord.service tjai-gunicorn-pyspy-watchdog.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tjai-gunicorn tjai-mcp-asgi tjai-tgbot tjai-supervisord tjai-gunicorn-pyspy-watchdog

   # Create admin user
   ./setup_superuser.sh
   ```

## Updating Production

After making changes in your dev checkout:

```bash
./deploy/update_from_dev.sh
```

Apache must route MCP before the general `/tjai/` Django proxy so MCP cannot
consume gunicorn workers:

```apache
ProxyPass        /tjai/mcp/ http://127.0.0.1:8003/
ProxyPassReverse /tjai/mcp/ http://127.0.0.1:8003/
ProxyPass        /tjai/mcp  http://127.0.0.1:8003/
ProxyPassReverse /tjai/mcp  http://127.0.0.1:8003/
```

## Verify Deployment

```bash
# Health check
curl -s https://etaverse.com/tjai/api/health
# Should return: {"status": "ok"}

# Authenticated sync probe without returning entry content
source ~/.env
curl -s -H "Authorization: Bearer $TJAI_API_KEY" \
  "https://etaverse.com/tjai/api/sync/pull?since=9999999999"

# MCP ASGI health check, local only
curl -s http://127.0.0.1:8003/health
```

## API Endpoints

- `GET /tjai/api/health` - Health check
- `POST /tjai/api/sync/push` - Push entries from client (REST bearer required)
- `GET /tjai/api/sync/pull?since=<timestamp>` - Pull entries modified since timestamp (REST bearer required)

## Log Files

Shared with primus:
- `/var/log/apache2/etaverse_ssl_access.log` - HTTPS requests
- `/var/log/apache2/etaverse_ssl_error.log` - HTTPS errors
