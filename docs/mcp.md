# MCP Server

tjai exposes a remote MCP endpoint at:

```text
https://etaverse.com/tjai/mcp/
```

The endpoint is for personal MCP clients such as Claude Code and local tooling.
It is not intended for claude.ai connectors or public unauthenticated access.

## Architecture

MCP is served by a standalone ASGI process, separate from the main Django web
pool:

```text
client
  -> Apache /tjai/mcp/
  -> 127.0.0.1:8003
  -> tjai-mcp-asgi.service
  -> uvicorn tjai_project.mcp_asgi:application
  -> FastMCP from the official mcp Python SDK
  -> tjai_app.mcp tool functions
  -> tjai_app.services / Django ORM / PostgreSQL
```

The main web app remains:

```text
Apache /tjai/
  -> 127.0.0.1:8002
  -> tjai-gunicorn.service
  -> tjai_project.wsgi_subpath:application
```

This split is deliberate. MCP bugs or long-running client behavior must not be
able to consume the gunicorn workers that serve the web UI and REST API.

## Transport Policy

tjai MCP is finite JSON request/response only:

- `POST` only.
- `GET`, `DELETE`, `OPTIONS`, and other methods are rejected with HTTP 405.
- Clients may send `Accept: application/json, text/event-stream`, as required
  by streamable HTTP MCP clients. FastMCP is configured to answer with JSON.
- Server-pushed MCP event streams are not used operationally.
- FastMCP is configured with `stateless_http=True` and `json_response=True`.

The useful MCP surface is:

- `initialize`
- `tools/list`
- `tools/call`

There is no operational dependency on MCP subscriptions, resource-change
notifications, progress streams, or server-pushed events.

## Authentication

Every MCP request except `/health` requires:

```text
Authorization: Bearer <token>
```

The token is read from `SysConfig` key `mcp_bearer_token`. Missing token returns
401, invalid token returns 403, and missing server-side configuration returns
503.

Rotate the token from production:

```bash
cd /var/www/tjai
.venv/bin/python manage.py shell -c "
import secrets, time
from tjai_app.models import SysConfig
tok = 'tjai_' + secrets.token_urlsafe(32)
SysConfig.objects.update_or_create(
    key='mcp_bearer_token',
    defaults={'value': tok, 'timestamp_modified': time.time()},
)
print(tok)
"
```

## Code Layout

- `tjai_app/mcp.py`: tool definitions and the `FastMCP` instance.
- `tjai_project/mcp_asgi.py`: ASGI entrypoint, auth guard, transport guard,
  `/health`, and path normalization.
- `deploy/tjai-mcp-asgi.service`: systemd unit for the MCP ASGI process.
- `deploy/update_from_dev.sh`: deploy script restart hook for MCP service and
  cleanup of the old `django-mcp-server` package.
- `requirements/base.txt`: depends on the official `mcp` Python SDK.

The old Django URL mount was removed from `tjai_project/urls.py`; `/mcp` should
not be served by the main Django/gunicorn app.

## Apache Routing

Apache must route MCP before the general `/tjai/` proxy:

```apache
ProxyPass        /tjai/mcp/ http://127.0.0.1:8003/
ProxyPassReverse /tjai/mcp/ http://127.0.0.1:8003/
ProxyPass        /tjai/mcp  http://127.0.0.1:8003/
ProxyPassReverse /tjai/mcp  http://127.0.0.1:8003/
```

The general `/tjai/` proxy should continue to point to gunicorn on port 8002.

## Deployment

Install the service file when provisioning or migrating the host:

```bash
sudo cp /var/www/tjai/deploy/tjai-mcp-asgi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tjai-mcp-asgi
```

Normal app deploy:

```bash
cd /home/admin/github/tjrepo/tjai
./deploy/update_from_dev.sh
```

The deploy script:

1. Rsyncs source to `/var/www/tjai`.
2. Installs requirements when requirements changed.
3. Uninstalls `django-mcp-server` if it remains in the venv.
4. Runs migrations and collectstatic.
5. Reloads gunicorn.
6. Restarts `tjai-mcp-asgi` if the service exists.
7. Restarts the telegram bot and action agent.

## Verification

Local service health:

```bash
curl -s http://127.0.0.1:8003/health
```

Expected response:

```json
{"status": "ok"}
```

Transport guard checks:

```bash
curl -i http://127.0.0.1:8003/
curl -i -X POST -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  http://127.0.0.1:8003/
curl -i -X POST -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  http://127.0.0.1:8003/
```

Expected statuses:

- unauthenticated `GET`: 405
- unauthenticated streamable HTTP `POST`: 401
- unauthenticated JSON-only `POST`: 401

Authenticated smoke test:

```bash
TOKEN='...'
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  http://127.0.0.1:8003/ | jq '.result.tools | length'
```

## Rollback

If the standalone MCP service fails, the safest immediate rollback is to remove
or disable the Apache `/tjai/mcp` proxy while leaving the main web app online:

```bash
sudo systemctl stop tjai-mcp-asgi
sudo systemctl reload apache2
```

Do not point `/tjai/mcp` back at gunicorn without reintroducing the failure mode
that originally let MCP consume the main web worker pool.
