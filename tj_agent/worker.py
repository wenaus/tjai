"""Remote inference worker — long-polls the tjai server for work items
and runs them through a local ollama instance, optionally as an agent
loop with MCP-provided tools (lxr code browser, github, etc.).

Enabled by setting worker_enabled=true in the tj config. Runs as a
daemon thread started from tj_agent.daemon.run_forever() that wraps an
asyncio event loop. Protocol:

  1. GET /api/worker/poll (long-polled, server holds up to 50s)
  2. Receive {"work": {entry_id, work_type, model, prompt, timeout_sec, ...}}
     The "model" field is the *capability name* (e.g. "gemma4"), which the
     worker maps to a local ollama model via worker_models config.
  3. Run an agentic chat loop against ollama /api/chat with all MCP tools
     advertised. The loop terminates when the model returns no further
     tool_calls (typical case for non-tool-using prompts: terminates after
     turn 1 with the assistant's text answer).
  4. POST the final answer (or error) to /api/worker/result.

Reconnection: transient failures (network, server 5xx) backoff exponentially
from 1s to 60s max. A successful poll resets the backoff.

MCP tool sources are configured by tj_agent.mcp_tool_dispatcher.default_server_specs()
— currently bundles lxr-mcp-server (Python) and github-mcp-server (Go),
each spawned as a stdio subprocess. Servers that fail to launch are
logged and skipped; the worker keeps running with whatever tools are
available (including zero, which collapses to single-shot inference).

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
      "gemma4":      "gemma4:31b",
      "gemma4-fast": "gemma4:e4b"
  }

Legacy schema (still accepted, deprecated — single model only):
  "worker_capabilities": ["gemma4"],
  "ollama_model":        "gemma4:e4b",
  "worker_max_tokens":   <int>          # optional
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from tj.config import get_config
from tj_agent import client
from tj_agent.sync import get_machine_id

logger = logging.getLogger(__name__)

POLL_CLIENT_TIMEOUT = 70          # must exceed server hold (50s)
POLL_BACKOFF_INITIAL = 1.0
POLL_BACKOFF_MAX = 60.0
DEFAULT_INFERENCE_TIMEOUT = 3600  # 60 min per ollama call — overridden by work.timeout_sec

# Agent loop has no turn cap. The natural termination is "model emits no
# tool_calls". The per-call ollama timeout above is the only wall-clock
# guard. A runaway loop is observable in the agent log (one tool_call line
# per turn) and can be killed manually; that is strictly better than
# aborting a legitimate long exploration just because it crossed an
# arbitrary integer.

# Source label used when this worker writes to tjai's central AppLog via
# /api/log. Lets ec2dev distinguish per-prompt events emitted from this
# Mac from the server-side dispatch/result events (which use 'worker').
TJAI_LOG_SOURCE = "worker-mac"


def _post_log_sync(message: str, level: str, extra_data: dict | None) -> None:
    """Blocking POST to tjai /api/log. Swallows all errors — logging
    failures must never disrupt work processing. Reads TJAI_API_KEY from
    the environment on each call so a token rotation in ~/.tjai/env takes
    effect on the next prompt without restarting the agent.

    Called from _log_to_tjai() via asyncio.to_thread().
    """
    token = os.environ.get("TJAI_API_KEY", "")
    if not token:
        return  # silently no-op when token not configured
    try:
        client.api_log(
            token=token,
            source=TJAI_LOG_SOURCE,
            message=message,
            level=level,
            extra_data=extra_data,
        )
    except Exception as e:
        logger.warning("worker: tjai api_log failed: %s", e)


async def _log_to_tjai(message: str, level: str = "info",
                       extra_data: dict | None = None) -> None:
    """Async wrapper around _post_log_sync. Use from inside the asyncio
    worker loop. Always returns; never raises."""
    try:
        await asyncio.to_thread(_post_log_sync, message, level, extra_data)
    except Exception as e:
        logger.warning("worker: _log_to_tjai dispatch failed: %s", e)


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


def _ollama_chat_sync(ollama_url: str, model: str, messages: list[dict],
                      tools: list[dict] | None,
                      timeout_sec: int,
                      max_tokens: int | None = None) -> dict:
    """Blocking POST to ollama /api/chat. Returns the parsed response dict.

    Called via asyncio.to_thread() from the async loop so the event loop
    isn't blocked while ollama generates. Raises on network/HTTP errors.
    """
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
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
    return json.loads(body)


async def _process_work_async(work: dict, machine_id: str, cfg: dict,
                              dispatcher) -> None:
    """Run one work item end-to-end as an agentic chat loop.

    The dispatcher (McpToolDispatcher | None) provides MCP-backed tools.
    When dispatcher is None or has zero tools, this collapses to a single
    /api/chat call (the old single-shot behavior). When tools are
    advertised and the model emits tool_calls, the loop dispatches each,
    appends the results to the message history, and re-prompts until
    the model stops emitting tool_calls. The only wall-clock guard is
    the per-call ollama timeout.

    Always reports a result (success or failure) so the server can
    unclaim the entry and update base tracking.
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
            await asyncio.to_thread(
                client.worker_result,
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
            await asyncio.to_thread(
                client.worker_result,
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=0)
        except Exception as e:
            logger.exception("failed to post failure result: %s", e)
        return

    timeout_sec = int(work.get("timeout_sec") or DEFAULT_INFERENCE_TIMEOUT)
    ollama_model = model_cfg["ollama_name"]
    max_tokens = model_cfg.get("max_tokens")
    work_type = work.get("work_type", "generic")

    tools = dispatcher.tools_for_ollama() if dispatcher else []

    logger.info(
        "Running %s work for %s via %s [cap=%s] "
        "(timeout %ds, %d prompt chars, %d tools)",
        work_type, entry_id, ollama_model, capability,
        timeout_sec, len(prompt), len(tools))
    hostname = socket.gethostname()
    await _log_to_tjai(
        f"received {work_type} prompt {entry_id} via {capability}={ollama_model} "
        f"({len(prompt)} chars, {len(tools)} tools available) on {hostname}",
        level="info",
        extra_data={
            "event": "received",
            "entry_id": entry_id,
            "machine_id": machine_id,
            "hostname": hostname,
            "capability": capability,
            "ollama_model": ollama_model,
            "work_type": work_type,
            "prompt_chars": len(prompt),
            "tools_advertised": len(tools),
        },
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    final_text = ""
    start = time.time()
    turns_used = 0
    tool_calls_total = 0

    try:
        while True:
            turns_used += 1
            response = await asyncio.to_thread(
                _ollama_chat_sync,
                ollama_url=cfg["ollama_url"],
                model=ollama_model,
                messages=messages,
                tools=tools or None,
                timeout_sec=timeout_sec,
                max_tokens=max_tokens,
            )
            assistant_msg = response.get("message") or {}
            # Always append the assistant turn so the model sees its own
            # prior tool_calls on the next iteration
            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                final_text = (assistant_msg.get("content") or "").strip()
                break

            # Dispatch each tool call and append a tool message per result
            for tc in tool_calls:
                tool_calls_total += 1
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                logger.info("%s: tool_call %s(%s)",
                            entry_id, name,
                            json.dumps(args)[:200] if args else "")
                try:
                    if dispatcher is None:
                        raise RuntimeError(
                            "model emitted tool_call but no MCP dispatcher")
                    tool_text = await dispatcher.call_tool(name, args)
                except Exception as e:
                    tool_text = f"TOOL ERROR: {type(e).__name__}: {e}"
                    logger.warning("%s: tool %s failed: %s",
                                   entry_id, name, e)
                # ollama /api/chat tool messages must include the tool name
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": tool_text,
                })
    except Exception as e:
        duration = int(time.time() - start)
        err = f"{type(e).__name__}: {e}"
        logger.error("%s: inference failed after %ds (turn %d): %s",
                     entry_id, duration, turns_used, err)
        await _log_to_tjai(
            f"failed {entry_id} after {duration}s "
            f"(turn {turns_used}, {tool_calls_total} tool call(s)): {err}",
            level="error",
            extra_data={
                "event": "failed",
                "entry_id": entry_id,
                "machine_id": machine_id,
                "hostname": hostname,
                "capability": capability,
                "ollama_model": ollama_model,
                "duration_sec": duration,
                "turns_used": turns_used,
                "tool_calls": tool_calls_total,
                "error": err,
            },
        )
        try:
            await asyncio.to_thread(
                client.worker_result,
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=duration)
        except Exception as re:
            logger.exception("failed to post failure result: %s", re)
        return

    duration = int(time.time() - start)
    if not final_text:
        # Model returned an empty final message — treat as failure so the
        # caller knows nothing useful came out
        err = (f"empty final response after {turns_used} turn(s), "
               f"{tool_calls_total} tool call(s)")
        logger.error("%s: %s", entry_id, err)
        await _log_to_tjai(
            f"failed {entry_id} after {duration}s: {err}",
            level="error",
            extra_data={
                "event": "failed",
                "entry_id": entry_id,
                "machine_id": machine_id,
                "hostname": hostname,
                "capability": capability,
                "ollama_model": ollama_model,
                "duration_sec": duration,
                "turns_used": turns_used,
                "tool_calls": tool_calls_total,
                "error": err,
            },
        )
        try:
            await asyncio.to_thread(
                client.worker_result,
                machine_id=machine_id, entry_id=entry_id,
                status="failed", error=err, duration_sec=duration)
        except Exception as e:
            logger.exception("failed to post failure result: %s", e)
        return

    logger.info("%s: done in %ds (%d turn(s), %d tool call(s), "
                "%d output chars)",
                entry_id, duration, turns_used, tool_calls_total, len(final_text))
    await _log_to_tjai(
        f"completed {entry_id} in {duration}s "
        f"({turns_used} turn(s), {tool_calls_total} tool call(s), "
        f"{len(final_text)} output chars) via {capability}={ollama_model}",
        level="info",
        extra_data={
            "event": "completed",
            "entry_id": entry_id,
            "machine_id": machine_id,
            "hostname": hostname,
            "capability": capability,
            "ollama_model": ollama_model,
            "duration_sec": duration,
            "turns_used": turns_used,
            "tool_calls": tool_calls_total,
            "output_chars": len(final_text),
        },
    )
    try:
        await asyncio.to_thread(
            client.worker_result,
            machine_id=machine_id, entry_id=entry_id,
            status="done", result=final_text, duration_sec=duration)
    except Exception as e:
        logger.exception("%s: failed to post result after %ds: %s",
                         entry_id, duration, e)


async def _run_worker_forever_async() -> None:
    """Async main loop: long-poll for work, dispatch, repeat.

    Holds one McpToolDispatcher for the lifetime of the loop. If MCP
    startup fails, the loop runs anyway with no tools available — every
    work item goes through single-shot inference. The dispatcher is torn
    down on loop exit (which only happens on cancellation).
    """
    machine_id = get_machine_id()
    backoff = POLL_BACKOFF_INITIAL

    # Lazy import — keeps mcp out of the import path for users who don't
    # enable the worker
    dispatcher = None
    try:
        from tj_agent.mcp_tool_dispatcher import (
            McpToolDispatcher, default_server_specs)
        dispatcher = McpToolDispatcher()
        await dispatcher.start(default_server_specs())
    except Exception as e:
        logger.exception(
            "worker: MCP tool dispatcher failed to start, continuing "
            "without tools: %s", e)
        dispatcher = None

    tool_count = dispatcher.tool_count if dispatcher else 0
    server_names = dispatcher.server_names if dispatcher else []
    logger.info(
        "worker: loop starting (machine_id=%s, mcp_servers=%s, tools=%d)",
        machine_id, server_names or "none", tool_count)

    try:
        while True:
            cfg = _get_worker_config()
            if not cfg["enabled"] or not cfg["models"]:
                await asyncio.sleep(30)  # config disabled — idle check every 30s
                continue
            capabilities = sorted(cfg["models"].keys())

            try:
                response = await asyncio.to_thread(
                    client.worker_poll,
                    machine_id=machine_id,
                    capabilities=capabilities,
                    timeout=POLL_CLIENT_TIMEOUT,
                )
                backoff = POLL_BACKOFF_INITIAL  # reset on successful poll
            except Exception as e:
                msg = str(e).lower()
                if "timed out" in msg or "timeout" in msg:
                    logger.debug(
                        "worker: poll timed out (expected), reconnecting")
                    continue
                logger.warning(
                    "worker: poll failed, backoff %ds: %s", int(backoff), e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, POLL_BACKOFF_MAX)
                continue

            work = response.get("work")
            if not work:
                continue  # no work in this hold window — reconnect immediately

            try:
                await _process_work_async(work, machine_id, cfg, dispatcher)
            except Exception as e:
                logger.exception(
                    "worker: unexpected error processing work: %s", e)
    finally:
        if dispatcher is not None:
            try:
                await dispatcher.close()
            except Exception as e:
                logger.warning("worker: dispatcher close error: %s", e)


def run_worker_forever() -> None:
    """Thread entry point — runs the async worker loop forever.

    Wraps the async loop in asyncio.run so the existing thread-based
    daemon startup in tj_agent.daemon stays unchanged.
    """
    try:
        asyncio.run(_run_worker_forever_async())
    except Exception as e:
        logger.exception("worker: async loop crashed: %s", e)


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
