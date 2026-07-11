"""Server API client using urllib (no external dependencies)."""

import json
import os
from functools import lru_cache
from pathlib import Path
import subprocess
import traceback
import urllib.request
import urllib.error

# Default server URL
DEFAULT_SERVER = "https://etaverse.com/tjai"


@lru_cache(maxsize=1)
def get_api_key() -> str:
    """Load the REST bearer from the process or standard local env files."""
    token = os.environ.get("TJAI_API_KEY") or os.environ.get(
        "TJAI_GMAIL_ADDON_API_KEY"
    )
    if token:
        return token

    for env_file in (Path.home() / ".tjai" / "env", Path.home() / ".env"):
        if not env_file.exists():
            continue
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                'set -a; source "$1" >/dev/null 2>&1; set +a; '
                'printf %s "${TJAI_API_KEY:-${TJAI_GMAIL_ADDON_API_KEY:-}}"',
                "bash",
                str(env_file),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout

    raise RuntimeError(
        "TJAI_API_KEY or TJAI_GMAIL_ADDON_API_KEY is required in the "
        "environment, ~/.tjai/env, or ~/.env"
    )


def api_headers(*, json_content: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {get_api_key()}"}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def get_server_url() -> str:
    """Get server URL from config or default."""
    try:
        from tj.config import get_config
        config = get_config()
        return config.get("sync_server", DEFAULT_SERVER)
    except Exception:
        traceback.print_exc()
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
        headers=api_headers(json_content=True),
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
