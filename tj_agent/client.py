"""HTTP client for tjai sync API using urllib (no external dependencies)."""

import json
import urllib.parse
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


def worker_poll(machine_id: str, capabilities: list[str],
                timeout: int = 70) -> dict[str, Any]:
    """Long-poll the server for remote inference work.

    Server holds the request up to ~50s waiting for a matching entry.
    Returns {"status": "ok", "work": {...} or None}.

    The client timeout should be 15-20s longer than the server hold to
    tolerate normal jitter; shorter values will spuriously disconnect.
    """
    caps = ",".join(capabilities)
    url = (f"{get_sync_server()}/api/worker/poll"
           f"?machine_id={urllib.parse.quote(machine_id)}"
           f"&capabilities={urllib.parse.quote(caps)}")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")


def api_log(token: str, source: str, message: str,
            level: str = "info",
            extra_data: dict | None = None) -> dict[str, Any]:
    """POST a log line to tjai's central AppLog via /api/log.

    Used by the remote inference worker to surface per-prompt events
    (received / completed / failed) in the same log stream as
    server-side events. Bearer-authenticated against SysConfig
    'gmail_addon_api_key' (same key the gmail addon uses).

    level: 'debug' | 'info' | 'warning' | 'error'
    """
    url = f"{get_sync_server()}/api/log"
    payload: dict[str, Any] = {
        "source": source,
        "message": message,
        "level": level,
    }
    if extra_data is not None:
        payload["extra_data"] = extra_data
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")


def worker_result(machine_id: str, entry_id: str, status: str,
                  result: str = "", error: str = "",
                  duration_sec: int = 0) -> dict[str, Any]:
    """Post the result of remote inference work back to the server.

    status must be 'done' or 'failed'. On 'done', result is the inference
    text. On 'failed', error is a human-readable error message.
    """
    url = f"{get_sync_server()}/api/worker/result"
    payload = {
        "machine_id": machine_id,
        "entry_id": entry_id,
        "status": status,
        "result": result,
        "error": error,
        "duration_sec": duration_sec,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise Exception(f"Server error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise Exception(f"Connection error: {e.reason}")
