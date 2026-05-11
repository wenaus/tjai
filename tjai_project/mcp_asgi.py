"""Standalone ASGI entrypoint for tjai's MCP server.

This process serves MCP separately from the main Django gunicorn pool. It uses
the official MCP Python SDK/FastMCP transport in stateless JSON-response mode,
with a small ASGI guard enforcing tjai's bearer token and POST-only policy.
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

from tjai_app.mcp import mcp  # noqa: E402
from tjai_app.models import SysConfig  # noqa: E402


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
    try:
        return SysConfig.objects.get(key="mcp_bearer_token").value
    except SysConfig.DoesNotExist:
        return None


class MCPRequestGuard:
    """Enforce auth and finite POST JSON-RPC before FastMCP sees a request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await _send_json(send, 200, {"status": "ok"})
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

    def _normalize_mcp_path(self, scope):
        """Accept common proxy forms: /, /mcp[/...], or /tjai/mcp[/...]."""
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        for prefix in ("/tjai/mcp", "/mcp"):
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

application = MCPRequestGuard(_mcp_application)
