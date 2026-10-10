"""Standalone ASGI entrypoint for tjai's MCP server.

This process serves MCP separately from the main Django gunicorn pool. It uses
the official MCP Python SDK/FastMCP transport in stateless JSON-response mode,
with a small ASGI guard enforcing tjai's bearer token and POST-only policy.

/tjai/mcp-oauth/ serves the same tools to clients that cannot send a fixed
token (ChatGPT): it accepts only tokens issued by tjai's OAuth sign-in, whose
endpoints this process also serves (tjai_app/oauth.py, docs/mcp.md § OAuth).
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tjai_project.settings.production")

import django
from asgiref.sync import sync_to_async
from starlette.applications import Starlette
from starlette.routing import Mount

django.setup()

from django.db import OperationalError, connections  # noqa: E402

from tjai_app import oauth  # noqa: E402
from tjai_app.mcp import mcp  # noqa: E402
from tjai_app.models import SysConfig  # noqa: E402

OAUTH_MCP_PREFIX = "/tjai/mcp-oauth"


def _json_body(value: dict[str, Any]) -> bytes:
    return json.dumps(value).encode("utf-8")


async def _send_json(send, status: int, value: dict[str, Any], headers=None) -> None:
    body = _json_body(value)
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if headers:
        response_headers.extend(headers)
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": response_headers,
    })
    await send({"type": "http.response.body", "body": body})


@sync_to_async
def _expected_token() -> str | None:
    # Belt-and-suspenders to CONN_HEALTH_CHECKS: if the cached DB connection
    # is dead, close all thread-local connections and retry once. Without
    # this, a single dropped Postgres socket wedges this worker permanently
    # (no Django request lifecycle here to reap stale conns).
    try:
        return SysConfig.objects.get(key="mcp_bearer_token").value
    except SysConfig.DoesNotExist:
        return None
    except OperationalError:
        connections.close_all()
        try:
            return SysConfig.objects.get(key="mcp_bearer_token").value
        except SysConfig.DoesNotExist:
            return None


class MCPRequestGuard:
    """Enforce auth and finite POST JSON-RPC before FastMCP sees a request."""

    def __init__(self, app, oauth_app):
        self.app = app
        self.oauth_app = oauth_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await _send_json(send, 200, {"status": "ok"})
            return

        if oauth.is_oauth_path(path):
            await self.oauth_app(scope, receive, send)
            return

        if path == OAUTH_MCP_PREFIX or path.startswith(OAUTH_MCP_PREFIX + "/"):
            await self._oauth_mcp(self._normalize_mcp_path(scope), receive, send)
            return

        scope = self._normalize_mcp_path(scope)
        method = scope.get("method", "").upper()
        if method != "POST":
            await _send_json(
                send,
                405,
                {
                    "error": "MCP endpoint accepts POST JSON-RPC only",
                    "allowed_methods": ["POST"],
                },
                headers=[(b"allow", b"POST")],
            )
            return

        headers = self._headers(scope)
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            await _send_json(send, 401, {"error": "Authorization required"})
            return

        expected = await _expected_token()
        if expected is None:
            await _send_json(send, 503, {"error": "MCP token not configured"})
            return

        if not hmac.compare_digest(auth_header[7:], expected):
            await _send_json(send, 403, {"error": "Invalid token"})
            return

        await self.app(scope, receive, send)

    async def _oauth_mcp(self, scope, receive, send):
        """The OAuth endpoint: an issued token first, so any unauthenticated
        request, GET included, gets the 401 that starts a client's sign-in."""
        auth_header = self._headers(scope).get("authorization", "")
        if not auth_header.startswith("Bearer "):
            await _send_json(
                send, 401, {"error": "Authorization required"},
                headers=[(b"www-authenticate", oauth.challenge().encode())],
            )
            return
        if not await oauth.access_allowed(auth_header[7:]):
            await _send_json(
                send, 401, {"error": "invalid_token"},
                headers=[(b"www-authenticate", oauth.challenge("invalid_token").encode())],
            )
            return
        if scope.get("method", "").upper() != "POST":
            await _send_json(
                send,
                405,
                {
                    "error": "MCP endpoint accepts POST JSON-RPC only",
                    "allowed_methods": ["POST"],
                },
                headers=[(b"allow", b"POST")],
            )
            return
        await self.app(scope, receive, send)

    def _normalize_mcp_path(self, scope):
        """Accept common proxy forms: /, /mcp[/...], /tjai/mcp[/...] or /tjai/mcp-oauth[/...]."""
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        for prefix in (OAUTH_MCP_PREFIX, "/tjai/mcp", "/mcp"):
            if path == prefix or path.startswith(prefix + "/"):
                scope = dict(scope)
                scope["root_path"] = root_path + prefix
                scope["path"] = path[len(prefix):] or "/"
                return scope
        return scope

    def _headers(self, scope) -> dict[str, str]:
        headers = {}
        for key, value in scope.get("headers", []):
            headers[key.decode("latin1").lower()] = value.decode("latin1")
        return headers


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


_mcp_application = Starlette(
    routes=[Mount("/", app=mcp.streamable_http_app())],
    lifespan=lifespan,
)

application = MCPRequestGuard(_mcp_application, Starlette(routes=oauth.routes()))
