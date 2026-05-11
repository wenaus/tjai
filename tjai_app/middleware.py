"""Bearer-token authentication middleware for the MCP endpoint.

Checks the Authorization: Bearer <token> header against the value stored in
SysConfig under key 'mcp_bearer_token'. Same shared-secret pattern used by
the rest of tjai's API endpoints (e.g. gmail_addon_api_key in views.py).

Single-user system, single token. Rotate by overwriting the SysConfig row.
"""

import hmac
import logging

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class MCPAuthMiddleware:
    """Require a valid bearer token on /mcp paths. Pass everything else through."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        script_name = settings.FORCE_SCRIPT_NAME or ""
        mcp_path = f"{script_name}/mcp"
        if not (request.path == mcp_path or request.path.startswith(mcp_path + "/")):
            return self.get_response(request)

        transport_response = self._validate_transport(request)
        if transport_response:
            return transport_response

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse({"error": "Authorization required"}, status=401)
        token = auth_header[7:]

        # Deferred import to avoid AppRegistryNotReady at module import time.
        from .models import SysConfig
        try:
            expected = SysConfig.objects.get(key="mcp_bearer_token").value
        except SysConfig.DoesNotExist:
            logger.error("MCP bearer token not configured in SysConfig")
            return JsonResponse({"error": "MCP token not configured"}, status=503)

        if not hmac.compare_digest(token, expected):
            return JsonResponse({"error": "Invalid token"}, status=403)

        return self.get_response(request)

    def _validate_transport(self, request):
        """Keep MCP as finite JSON POST request/response; no GET/SSE streams."""
        if request.method != "POST":
            response = JsonResponse(
                {
                    "error": "MCP endpoint accepts POST JSON-RPC only",
                    "allowed_methods": ["POST"],
                },
                status=405,
            )
            response["Allow"] = "POST"
            return response

        accept = request.META.get("HTTP_ACCEPT", "")
        if any(
            part.split(";", 1)[0].strip().lower() == "text/event-stream"
            for part in accept.split(",")
        ):
            return JsonResponse(
                {"error": "MCP server-pushed event streams are not supported"},
                status=406,
            )

        return None
