# MCP Server

tjai exposes a remote MCP endpoint at:

```text
https://etaverse.com/tjai/mcp/
```

The endpoint is for personal MCP clients such as Claude Code and local tooling.
It is not intended for claude.ai connectors or public unauthenticated access.

> **Scope.** This document describes the MCP server tjai *exposes* — its tools,
> transport, auth, and deployment. A separate, read-only **Postgres MCP** that
> Claude Code uses to query the database directly (bypassing the ORM/REST layer)
> is described in [Related: Direct Database MCP](#related-direct-database-mcp)
> below.

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

## Tools

36 tools are registered, defined in `tjai_app/mcp.py`:

- Read: `get_server_instructions`, `get_calendar`, `get_profile`,
  `get_ai_guidance`, `list_contexts`, `get_todos`, `get_todo_bangs`,
  `get_memories`,
  `get_dialog`, `get_logs`, `get_capcom`, `get_bookmarks`, `search_entries`,
  `get_named_entries`, `get_entry`, `get_entry_by_entry_id`, `get_goal`,
  `get_relations`, `get_relation_graph`, `get_entry_versions`
- Write: `create_entry`, `edit_entry`, `edit_entry_metadata`,
  `replace_entry_content`, `replace_text_in_entry`, `inflight_item`,
  `replace_section_in_entry`, `append_entry_content`, `copy_calendar_entry`,
  `change_entry_kind`, `delete_entry`, `create_goal`, `create_relation`,
  `edit_relation`, `delete_relation`, `restore_version`
- Execute: `run_action`

The startup tools `get_profile` and `get_ai_guidance` return size-bounded
pages with a 12,000-character budget per response; clients follow
`next_offset` until `complete` is true.

## Transport Policy

tjai MCP is finite JSON request/response only:

- `POST` only.
- `GET`, `DELETE`, `OPTIONS`, and other methods are rejected with HTTP 405.
- Clients may send `Accept: application/json, text/event-stream`, as required
  by streamable HTTP MCP clients. FastMCP is configured to answer with JSON.
- Server-pushed MCP event streams are not used operationally.
- FastMCP is configured with `stateless_http=True` and `json_response=True`.
- FastMCP validates the Host header against a `TransportSecuritySettings`
  allow-list built from Django `ALLOWED_HOSTS` plus localhost forms.
- An HTTP 202 response from FastMCP is normal and not a sign that SSE has
  returned. The MCP streamable-HTTP transport returns 202 for client-to-server
  notification frames such as `notifications/initialized` (the no-op
  acknowledgement that follows a successful `initialize` handshake). Each fresh
  client session produces one such 202 immediately after its first 200. This is
  request/response, not a server-pushed event stream.

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
503. The token lookup retries once after closing stale database connections,
so a dropped Postgres socket does not wedge an ASGI worker.

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
7. Restarts the telegram bot and schedules a graceful action-agent restart
   via the `action_agent_restart_requested` SysConfig flag; the agent
   finishes any in-progress action before restarting.

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

## Related: Direct Database MCP

Distinct from the MCP server tjai exposes, Claude Code clients also use a
read-only **Postgres MCP** to query the tjai database directly — schema
inspection, ad-hoc `SELECT`, and index/health analysis without going through the
Django ORM or REST layer:

```text
Claude Code
  -> postgres-mcp (--access-mode restricted)
  -> PostgreSQL (tjai database)
```

This is a generic third-party server (`postgres-mcp`, "Postgres MCP Pro"), not
tjai code, and a per-developer client tool rather than a deployed service. It is
read-only by policy. Tools it exposes: `execute_sql`, `list_schemas`,
`list_objects`, `get_object_details`, `explain_query`, `analyze_db_health`,
`analyze_query_indexes`, `analyze_workload_indexes`, `get_top_queries`.

### When to use it

The tjai MCP tools are the primary interface to tjai data; reach for them
first. The Postgres MCP is a read-only second-tier supplement, justified only
when the tjai tools genuinely cannot reach the data — schema inspection, ad-hoc
aggregation across many rows, or a table no tool exposes. Falling back to it
because a routine read has no tjai tool is a gap to close, not a habit to keep:
report it and add the tool rather than normalizing raw SQL. `get_logs` exists
for exactly this reason — log reads kept dropping to `execute_sql` against the
`applog` table because no tool exposed it.

Install and registration are in
[configuration.md → Database MCP](configuration.md#database-mcp-read-only-sql-access).
