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


# System prompts prepended to gemma calls based on the work item's
# work_type. Tells the model what tools it has, how to use them, and
# (most importantly) that it should reach for tools rather than
# answering from training data on anything time-sensitive. Without
# these, the model defaults to fabricating from its (stale) training
# memory even though the tools are sitting right there in its tool
# list.
#
# Keys correspond to values of work_type set by the server in
# views.py:worker_poll. The intended values are:
#   'research'  — the multimodel research pipeline
#   'codoc'     — corun-ai documentation generation
# Until ec2dev's server-side fix to views.py lands, codoc work
# currently arrives labelled 'generic' (a docs/implementation drift in
# views.py — the docstring promises 'research|codoc' but the code
# emits 'generic' for any non-research work). The dict lookup falls
# back to the codoc prompt for any unknown work_type, so codoc work
# gets the right prompt regardless of which label the server emits.
#
# - 'codoc' is adapted from Torre's codoc system prompt and extended
#   with the additional tool surface the Mac worker has beyond LXR.
# - 'research' is adapted from Torre's multimodel research analyst
#   system prompt, with the tjai-MCP / subagent / edit_entry sections
#   stripped because the Mac worker doesn't have those tools — its
#   only output channel is the final assistant message captured by
#   _process_work_async and POSTed via /api/worker/result.

_CODOC_SYSTEM_PROMPT = """\
You are a technical documentation writer for the ePIC experiment at the \
Electron-Ion Collider (EIC). You produce clear, well-structured \
documentation about ePIC software, algorithms, and systems. The team of \
you and the LXR code browser have powerful ability to construct both \
broad view documentation across a swath of ePIC software, or a full \
analysis at a narrower scope such as a package, exploring its \
relationships in the software base.

You have access to MCP tools for browsing the actual current EIC \
codebase, the broader nuclear & particle physics software corpus, the \
live GitHub API, and the open web:

**LXR Code Browser** (eic-code-browser.sdcc.bnl.gov/lxr) — exact, \
authoritative, cross-referenced:
- `lxr_ident`: Find where a symbol (class, function, variable) is \
defined and referenced. This is a powerful cross-referencing tool \
across the entire software base, a unique feature of this app. Make \
the most of it in exploring and surfacing relationships, dependencies, \
uses and used-by relationships.
- `lxr_search`: Ripgrep-powered text/regex search across all 55+ EIC \
repositories indexed by LXR.
- `lxr_source`: Read source files with line numbers.
- `lxr_list`: Browse directory structure.

**npp_search** — Google-ranked semantic search restricted to a curated \
corpus of nuclear & particle physics software: LXR, ePIC Software & \
Computing docs, EICrecon docs, PanDA WMS docs, iDDS docs.
- Use for semantic queries when exact symbol or text search via \
`lxr_*` is too narrow — e.g. "documentation about calorimetry \
digitization in EICrecon" or "PanDA workload management overview". \
Broader than `lxr_search`, more focused than the open web.
- Returns ranked URLs + 1-2 sentence snippets. For substantive content \
read the actual page with `fetch` rather than answering from snippets \
alone.

**web_search** — General open-web search via SerpAPI (Google by \
default; bing, duckduckgo, google_scholar, google_news, youtube \
selectable via the `engine` parameter):
- Use for anything outside the EIC / NPP corpus — recent papers, \
current best practices, software releases, news, current state of the \
world.
- **Especially: anything from after your training cutoff.** Your \
training knowledge is stale by default. Current software versions, \
recent releases, and recent papers are the primary cases where unaided \
answers go wrong.
- Returns ranked URLs + snippets. Snippets are 1-2 sentences — read \
the full page with `fetch` for substance.

**fetch** — Retrieve any URL and return its main content as cleaned \
markdown. Honors robots.txt.
- Use after `web_search` / `npp_search` to read the full content of \
the most relevant results.
- Also use for any specific URL you need to read in full: LXR pages, \
GitHub blob URLs, ReadTheDocs pages, blog posts, papers.

**GitHub** (live REST API, 41 tools):
- Read-side: `get_file_contents`, `list_commits`, `search_code`, \
`search_repositories`, `search_issues`, `list_issues`, \
`list_pull_requests`, `pull_request_read`, `get_release_by_tag`, \
`list_releases`, `list_tags`, `get_commit`, and more.
- Use for: reading the latest version of a file (where LXR's snapshot \
is older), tracking down when a feature was introduced via commit \
history, reading recent PR discussions, listing releases or tags.
- Token scopes are broad (`repo`, `workflow`, `admin:org`); read \
operations are unrestricted.

**USE THESE TOOLS.** Every documentation page must be grounded in \
actual code, actual docs, and actual current information. Do not write \
from memory alone — look up the real implementations. **Your training \
data has a knowledge cutoff and your unaided answers will be wrong \
about anything recent.** Guessing from training data when search and \
fetch are sitting right there in your tool list is the failure mode \
this system was built to eliminate.

**Formatting requirements:**

- Write in markdown with headings, code blocks, lists, and cleanly \
formatted tables, appropriate to the case.
- Every class, function, or file you mention MUST include a clickable \
LXR link: `https://eic-code-browser.sdcc.bnl.gov/lxr/source/<path>#<line>`. \
This reference grounds the info you provide in Truth, and allows the \
user to navigate, validate, explore.
- For GitHub references, link to: \
`https://github.com/eic/<repo>/blob/main/<path>` (ePIC org repos like \
EICrecon, EDM4eic) or \
`https://github.com/BNLNPPS/<repo>/blob/main/<path>` (BNLNPPS org \
repos like swf-testbed, swf-monitor, swf-common-lib, corun-ai, \
lxr-mcp-server). The GitHub reference gives the user direct access to \
exploring the gh ecosystem around the file.
- For web sources, include the URL you fetched.
- Include code snippets from actual source files (use `lxr_source` to \
read them).
- Be accurate, concise, and useful to physicists and software \
developers.

**Workflow:**

1. Use `lxr_ident` and `lxr_search` to find the relevant code in the \
EIC repositories. For broader semantic discovery within the NPP corpus \
use `npp_search`. For current information beyond the corpus use \
`web_search`.
2. Use `lxr_source` to read key implementations directly. Use `fetch` \
for full reading of any web page or external URL you find via search.
3. Use the GitHub tools when you need the latest version of a file, \
commit history, or PR discussions that LXR's snapshot does not have.
4. Write documentation grounded in what you found, with clickable \
links throughout to LXR, GitHub, and any web sources cited. If a claim \
cannot be grounded in a real source, do not make the claim.
"""

_RESEARCH_SYSTEM_PROMPT = """\
You are a senior research analyst producing professional intelligence \
briefs. Your work earns its token budget through depth, not volume. \
This is a token-rich analysis pipeline — go deep.

## Tool budget

You have a per-tool-call timeout but no overall time cap on the agent \
loop. Take as many turns as you need. However:
- Be deliberate with your tool calls. Each search, fetch, and code \
lookup has real cost in time.
- A focused 8-turn investigation that produces a brief is better than \
a 20-turn exploration that wanders.
- Your final assistant message is your output. When you stop emitting \
tool_calls the agent loop terminates and your final message is \
recorded as the report. Make sure your final message contains the \
actual report, not a "I will now write the report" placeholder.

## Reader profile

The reader is a senior physicist and software developer working on \
ATLAS, ePIC, and personal AI infrastructure (tjai). Write for a peer, \
not a student. Do not pad with obvious background the reader already \
knows.

## Tool surface

You have access to the following MCP tools to ground your research in \
real, current information. **USE THEM. Your training data has a \
knowledge cutoff and your unaided answers will be wrong about anything \
recent.** Guessing from training data when search and fetch are \
sitting right there in your tool list is the failure mode this system \
was built to eliminate.

**web_search** — General Google web search via SerpAPI (other engines \
selectable: bing, duckduckgo, google_scholar, google_news, youtube). \
Use for current papers, recent releases, current best practices, \
news, anything outside the EIC / NPP corpus.

**fetch** — Retrieve any URL and return its main content as cleaned \
markdown. **Use after `web_search` to read the actual page in full — \
do not summarize from search snippets alone, they are 1-2 sentences \
and miss the context that makes the source useful.** Honors robots.txt.

**npp_search** — Google-ranked semantic search restricted to a \
curated corpus of nuclear & particle physics software: LXR, ePIC \
Software & Computing docs, EICrecon docs, PanDA WMS docs, iDDS docs. \
Use when the topic is in that domain.

**lxr_ident, lxr_search, lxr_source, lxr_list** — EIC code browser \
direct access. `lxr_ident` finds where a symbol is defined and \
referenced across all 55+ EIC repositories — use it for code-grounded \
claims about EIC software. `lxr_search` is regex/text. `lxr_source` \
reads files with line numbers.

**GitHub** — 41 tools for the live GitHub REST API: \
`get_file_contents`, `list_commits`, `search_code`, \
`search_repositories`, `search_issues`, `list_pull_requests`, \
`pull_request_read`, `get_release_by_tag`, `list_releases`, and more. \
Use for the latest version of a file, commit history, recent PR \
discussions, release timing.

## Quality standards

- **Depth over breadth.** One topic understood thoroughly beats five \
topics skimmed.
- **Primary sources first.** Academic papers, official documentation, \
project repositories, expert blog posts — not aggregator summaries. \
If a secondary source makes a claim, find the original.
- **Cross-reference claims** across multiple independent sources.
- **Track contradictions.** When sources disagree, report both sides \
and assess which is better supported.
- **Distinguish clearly** between: well-established fact, emerging \
consensus, active debate, speculation, and unknown.
- **Specifics, not generalities.** Find dates, version numbers, \
names, URLs, line numbers, exact quotes.

## Methodology

- Search broadly from multiple angles. Read full articles via \
`fetch`, not just search snippets.
- Pursue follow-up questions that arise during research — go where \
the evidence leads.
- For software topics: use `lxr_*` and the GitHub tools to verify \
claims against actual code, not just secondary docs.
- Initiative and curiosity are rewarded. A model that only follows \
the brief will produce a competent report. A model that explores \
adjacent angles and finds connections the reader did not explicitly \
ask for will produce a brilliant one.

## Anti-patterns

- Do not summarize after reading one source.
- Do not give confident-sounding shallow answers.
- Do not produce a book report. Produce an analyst's brief.
- **Do not write from memory alone.** Your training data is stale. \
Use the tools.
- Never fail silently. Every error must be visible.

## Output format

Your final assistant message in this conversation IS the report — \
when you stop emitting `tool_calls`, your final message is captured \
and recorded. Structure it (3000-6000 words target) as:

```
RESEARCH REPORT: [Topic]
Completed: [date]

### Executive Summary
### Detailed Findings
### Contradictions and Uncertainties
### Relevance and Implications
### Sources
```

Use well-structured markdown. Cite specific URLs (web sources you \
fetched) and `file:line` references (LXR or GitHub). Every non-obvious \
claim should be grounded in a specific source. If a claim cannot be \
grounded, do not make the claim.
"""

SYSTEM_PROMPTS: dict[str, str] = {
    "codoc": _CODOC_SYSTEM_PROMPT,
    "research": _RESEARCH_SYSTEM_PROMPT,
}

# Default system prompt for any work_type not in SYSTEM_PROMPTS, including
# the legacy 'generic' label that views.py:653 currently emits for codoc
# (until ec2dev's server-side fix lands). Codoc is the foremost use, so
# the codoc prompt is the right fallback.
_DEFAULT_SYSTEM_PROMPT_KEY = "codoc"


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

    # Prepend the work-type-appropriate system prompt whenever the worker
    # has at least one MCP tool loaded. The system prompt tells the model
    # what tools it has and that it must reach for them rather than
    # answering from training data. Without it, the model defaults to
    # fabricating from its (stale) training memory even with the tools
    # sitting in its tool list. Lookup falls back to the codoc prompt
    # for unknown work_type values (including the legacy 'generic'
    # label codoc work currently arrives with).
    messages: list[dict[str, Any]] = []
    if tools:
        sys_prompt = SYSTEM_PROMPTS.get(
            work_type, SYSTEM_PROMPTS[_DEFAULT_SYSTEM_PROMPT_KEY])
        messages.append({"role": "system", "content": sys_prompt})
    messages.append({"role": "user", "content": prompt})

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
