"""Server API client using urllib (no external dependencies)."""

import json
import urllib.request
import urllib.error

# Default server URL
DEFAULT_SERVER = "https://etaverse.com/tjai"


def get_server_url() -> str:
    """Get server URL from config or default."""
    try:
        from tj.config import get_config
        config = get_config()
        return config.get("sync_server", DEFAULT_SERVER)
    except Exception:
        return DEFAULT_SERVER


def send_command(command: str, **kwargs) -> dict:
    """
    Send a command to the server.

    Args:
        command: Command name (e.g., "set_sysconfig", "get_sysconfig")
        **kwargs: Command-specific parameters

    Returns:
        Server response as dict

    Raises:
        Exception on network or server error
    """
    url = f"{get_server_url()}/api/command"
    payload = {"command": command, **kwargs}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")
