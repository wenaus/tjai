"""Remote inference worker — long-polls the tjai server for work items
and runs them through a local ollama instance.

Enabled by setting worker_enabled=true in the tj config. Runs as a thread
started from tj_agent.daemon.run_forever(). Protocol:

  1. GET /api/worker/poll (long-polled, server holds up to 50s)
  2. Receive {"work": {entry_id, work_type, model, prompt, timeout_sec, ...}}
     The "model" field is the *capability name* (e.g. "gemma4"), which the
     worker maps to a local ollama model via worker_models config.
  3. POST to ollama /api/chat with the resolved model and prompt
  4. POST the result (or error) to /api/worker/result

Reconnection: transient failures (network, server 5xx) backoff exponentially
from 1s to 60s max. A successful poll resets the backoff.

Config keys (tj config.json):
  worker_enabled  bool   — master switch (default False)
  ollama_url      str    — default "http://localhost:11434"
  worker_models   dict   — maps capability name → ollama model. Each value is
                           either a string (the ollama model name) or a dict
                           {"ollama_name": "...", "max_tokens": <int|null>}.

Example multi-model config:
  "worker_enabled": true,
  "ollama_url": "http://localhost:11434",
  "worker_models": {
      "gemma4":      "gemma4:e4b",
      "llama3.1":    {"ollama_name": "llama3.1:8b"},
      "deepseek-r1": {"ollama_name": "deepseek-r1:14b", "max_tokens": 8000}
  }

Legacy schema (still accepted, deprecated — single model only):
  "worker_capabilities": ["gemma4"],
  "ollama_model":        "gemma4:e4b",
  "worker_max_tokens":   <int>          # optional
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
    """Load worker config and normalize to a single internal shape.

    Returns:
        {
            "enabled":    bool,
            "ollama_url": str,
            "models":     {capability: {"ollama_name": str,
                                        "max_tokens": int | None}},
        }

    Accepts both the new `worker_models` dict and the legacy
    `worker_capabilities` + `ollama_model` + `worker_max_tokens` keys.
    The new schema wins if both are present.
    """
    config = get_config()
    enabled = bool(config.get("worker_enabled", False))
    ollama_url = config.get("ollama_url", "http://localhost:11434")

    models: dict[str, dict] = {}
    raw_models = config.get("worker_models")

    if isinstance(raw_models, dict) and raw_models:
        for cap, val in raw_models.items():
            if isinstance(val, str) and val:
                models[cap] = {"ollama_name": val, "max_tokens": None}
            elif isinstance(val, dict) and val.get("ollama_name"):
                mt = val.get("max_tokens")
                models[cap] = {
                    "ollama_name": str(val["ollama_name"]),
                    "max_tokens": int(mt) if mt is not None else None,
                }
            else:
                logger.warning(
                    "worker: ignoring malformed worker_models[%r] = %r",
                    cap, val)
    else:
        # Legacy single-model schema
        legacy_caps = list(config.get("worker_capabilities", []))
        legacy_model = config.get("ollama_model")
        legacy_max = config.get("worker_max_tokens")
        if legacy_caps and legacy_model:
            if len(legacy_caps) > 1:
                logger.warning(
                    "worker: legacy config has %d capabilities but a single "
                    "ollama_model=%r — ALL will run that model. Switch to "
                    "worker_models for true multi-model support.",
                    len(legacy_caps), legacy_model)
            mt = int(legacy_max) if legacy_max is not None else None
            for cap in legacy_caps:
                models[cap] = {"ollama_name": legacy_model, "max_tokens": mt}

    return {
        "enabled": enabled,
        "ollama_url": ollama_url,
        "models": models,
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

    capability = work.get("model") or ""
    model_cfg = cfg["models"].get(capability)
    if not model_cfg:
        err = (f"unknown capability {capability!r} — worker has "
               f"{sorted(cfg['models'].keys())}")
        logger.error("%s: %s", entry_id, err)
        try:
            client.worker_result(
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=0)
        except Exception as e:
            logger.exception("failed to post failure result: %s", e)
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
    ollama_model = model_cfg["ollama_name"]
    max_tokens = model_cfg.get("max_tokens")
    work_type = work.get("work_type", "generic")

    logger.info("Running %s work for %s via %s [cap=%s] "
                "(timeout %ds, %d prompt chars)",
                work_type, entry_id, ollama_model, capability,
                timeout_sec, len(prompt))
    start = time.time()
    try:
        result = _call_ollama(
            ollama_url=cfg["ollama_url"],
            model=ollama_model,
            prompt=prompt,
            timeout_sec=timeout_sec,
            max_tokens=max_tokens,
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
        if not cfg["enabled"] or not cfg["models"]:
            time.sleep(30)  # config disabled — idle check every 30s
            continue
        capabilities = sorted(cfg["models"].keys())

        try:
            response = client.worker_poll(
                machine_id=machine_id,
                capabilities=capabilities,
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
    if not cfg["models"]:
        logger.warning("worker: enabled but no models configured "
                       "(set worker_models in config.json)")
        return None

    thread = threading.Thread(
        target=run_worker_forever, name="tj_agent-worker", daemon=True)
    thread.start()
    summary = ", ".join(
        f"{cap}={mc['ollama_name']}"
        for cap, mc in sorted(cfg["models"].items()))
    logger.info("worker: thread started (%s)", summary)
    return thread
