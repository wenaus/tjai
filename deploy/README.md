# tjai Deployment

Production runs from `/var/www/tjai` on etaverse.com.

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

   # Create admin user
   ./setup_superuser.sh
   ```

## Updating Production

After making changes in your dev checkout:

```bash
./deploy/update_from_dev.sh
```

## Verify Deployment

```bash
# Health check
curl -s https://etaverse.com/tjai/api/health
# Should return: {"status": "ok"}

# Sync pull (empty DB)
curl -s "https://etaverse.com/tjai/api/sync/pull?since=0"
```

## API Endpoints

- `GET /tjai/api/health` - Health check
- `POST /tjai/api/sync/push` - Push entries from client
- `GET /tjai/api/sync/pull?since=<timestamp>` - Pull entries modified since timestamp

## Log Files

Shared with primus:
- `/var/log/apache2/etaverse_ssl_access.log` - HTTPS requests
- `/var/log/apache2/etaverse_ssl_error.log` - HTTPS errors
