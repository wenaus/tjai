"""HTTP client for tjai sync API."""

import json
import requests
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
    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def pull(machine_id: str, since: float) -> dict[str, Any]:
    """
    Pull entries modified since timestamp.

    Returns server response dict with entries, contexts, tags, sub_notes, server_time.
    """
    url = f"{get_sync_server()}/api/sync/pull"
    params = {
        "machine_id": machine_id,
        "since": since,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
