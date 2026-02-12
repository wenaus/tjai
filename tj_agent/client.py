"""HTTP client for tjai sync API using urllib (no external dependencies)."""

import json
import urllib.request
import urllib.error
from typing import Any

from tj.config import get_config


def get_sync_server() -> str:
    """Get sync server URL from config."""
    config = get_config()
    return config.get("sync_server", "https://etaverse.com/tjai")


def push(machine_id: str, hostname: str, entries: list, contexts: list,
         tags: list, sub_notes: list) -> dict[str, Any]:
    """
    Push dirty entries to server.

    Returns server response dict or raises on error.
    """
    url = f"{get_sync_server()}/api/sync/push"
    payload = {
        "machine_id": machine_id,
        "hostname": hostname,
        "entries": entries,
        "contexts": contexts,
        "tags": tags,
        "sub_notes": sub_notes,
    }

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
        raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")


def pull(machine_id: str, since: float, after_id: str = "") -> dict[str, Any]:
    """
    Pull entries modified since timestamp, with cursor-based pagination.

    Returns server response dict with entries, contexts, tags, sub_notes,
    server_time, and has_more flag.
    """
    url = f"{get_sync_server()}/api/sync/pull?machine_id={machine_id}&since={since}"
    if after_id:
        url += f"&after_id={after_id}"

    req = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")
