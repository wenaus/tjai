"""Remote inference worker — long-polls the tjai server for work items
and runs them through a local ollama instance.

Enabled by setting worker_enabled=true in the tj config. Runs as a thread
started from tj_agent.daemon.run_forever(). Protocol:

  1. GET /api/worker/poll (long-polled, server holds up to 50s)
  2. Receive {"work": {entry_id, work_type, model, prompt, timeout_sec, ...}}
  3. POST to ollama /api/chat with the model and prompt
  4. POST the result (or error) to /api/worker/result

Reconnection: transient failures (network, server 5xx) backoff exponentially
from 1s to 60s max. A successful poll resets the backoff.

Config keys (tj config.json):
  worker_enabled      bool   — master switch (default False)
  worker_capabilities list   — e.g. ["gemma4"]
  ollama_url          str    — default "http://localhost:11434"
  ollama_model        str    — default "gemma4:e4b"
  worker_max_tokens   int    — optional, passed to ollama options.num_predict
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from tj.config import get_config
from tj_agent import client
from tj_agent.sync import get_machine_id

logger = logging.getLogger(__name__)

POLL_CLIENT_TIMEOUT = 70          # must exceed server hold (50s)
POLL_BACKOFF_INITIAL = 1.0
POLL_BACKOFF_MAX = 60.0
DEFAULT_INFERENCE_TIMEOUT = 1800  # 30 min — overridden by work.timeout_sec


def _get_worker_config() -> dict:
    """Load worker config with sensible defaults."""
    config = get_config()
    return {
        "enabled": bool(config.get("worker_enabled", False)),
        "capabilities": list(config.get("worker_capabilities", [])),
        "ollama_url": config.get("ollama_url", "http://localhost:11434"),
        "ollama_model": config.get("ollama_model", "gemma4:e4b"),
        "max_tokens": config.get("worker_max_tokens"),
    }


def _call_ollama(ollama_url: str, model: str, prompt: str,
                 timeout_sec: int, max_tokens: int | None = None) -> str:
    """Call ollama /api/chat with a single user message, return response text.

    Raises on network/HTTP error, non-200, or empty response.
    """
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if max_tokens:
        payload["options"] = {"num_predict": int(max_tokens)}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    resp = json.loads(body)
    message = resp.get("message") or {}
    content = message.get("content", "") or ""
    if not content:
        raise RuntimeError(f"ollama returned empty content: {resp}")
    return content


def _process_work(work: dict, machine_id: str, cfg: dict) -> None:
    """Run one work item end-to-end.

    Always reports a result (success or failure) so the server can unclaim
    the entry and update base tracking.
    """
    entry_id = work.get("entry_id")
    if not entry_id:
        logger.error("work item missing entry_id: %s", work)
        return

    prompt = work.get("prompt") or ""
    if not prompt:
        err = "work item has empty prompt"
        logger.error("%s: %s", entry_id, err)
        try:
            client.worker_result(
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=0)
        except Exception as e:
            logger.exception("failed to post failure result: %s", e)
        return

    timeout_sec = int(work.get("timeout_sec") or DEFAULT_INFERENCE_TIMEOUT)
    model = cfg["ollama_model"]  # local override — server passes capability name
    work_type = work.get("work_type", "generic")

    logger.info("Running %s work for %s via %s (timeout %ds, %d prompt chars)",
                work_type, entry_id, model, timeout_sec, len(prompt))
    start = time.time()
    try:
        result = _call_ollama(
            ollama_url=cfg["ollama_url"],
            model=model,
            prompt=prompt,
            timeout_sec=timeout_sec,
            max_tokens=cfg.get("max_tokens"),
        )
    except Exception as e:
        duration = int(time.time() - start)
        err = f"{type(e).__name__}: {e}"
        logger.error("%s: inference failed after %ds: %s", entry_id, duration, err)
        try:
            client.worker_result(
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=duration)
        except Exception as re:
            logger.exception("failed to post failure result: %s", re)
        return

    duration = int(time.time() - start)
    logger.info("%s: done in %ds (%d output chars)",
                entry_id, duration, len(result))
    try:
        client.worker_result(
            machine_id=machine_id, entry_id=entry_id,
            status="done", result=result, duration_sec=duration)
    except Exception as e:
        logger.exception("%s: failed to post result after %ds: %s",
                         entry_id, duration, e)


def run_worker_forever() -> None:
    """Main loop: long-poll for work, process, repeat.

    Intended to run on a daemon thread started by tj_agent.daemon. Config is
    re-read on each iteration so enabling/disabling via config.json takes
    effect without restarting the sync daemon.
    """
    machine_id = get_machine_id()
    backoff = POLL_BACKOFF_INITIAL
    logger.info("worker: loop starting (machine_id=%s)", machine_id)

    while True:
        cfg = _get_worker_config()
        if not cfg["enabled"] or not cfg["capabilities"]:
            time.sleep(30)  # config disabled — idle check every 30s
            continue

        try:
            response = client.worker_poll(
                machine_id=machine_id,
                capabilities=cfg["capabilities"],
                timeout=POLL_CLIENT_TIMEOUT,
            )
            backoff = POLL_BACKOFF_INITIAL  # reset on successful poll
        except Exception as e:
            # Distinguish transient socket timeouts (expected at end of hold
            # window) from real errors. A urllib socket timeout is a normal
            # outcome if the server's hold window expired before the client
            # read — just reconnect immediately.
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                logger.debug("worker: poll timed out (expected), reconnecting")
                continue
            logger.warning("worker: poll failed, backoff %ds: %s", int(backoff), e)
            time.sleep(backoff)
            backoff = min(backoff * 2, POLL_BACKOFF_MAX)
            continue

        work = response.get("work")
        if not work:
            continue  # no work in this hold window — reconnect immediately

        try:
            _process_work(work, machine_id, cfg)
        except Exception as e:
            logger.exception("worker: unexpected error processing work: %s", e)


def start_worker_thread() -> threading.Thread | None:
    """Start the worker loop on a daemon thread if enabled in config.

    Called from tj_agent.daemon.run_forever(). Returns the thread (for
    observability) or None if disabled.
    """
    cfg = _get_worker_config()
    if not cfg["enabled"]:
        logger.info("worker: disabled (worker_enabled=false)")
        return None
    if not cfg["capabilities"]:
        logger.warning("worker: enabled but no capabilities configured")
        return None

    thread = threading.Thread(
        target=run_worker_forever, name="tj_agent-worker", daemon=True)
    thread.start()
    logger.info("worker: thread started (capabilities=%s, model=%s)",
                cfg["capabilities"], cfg["ollama_model"])
    return thread
