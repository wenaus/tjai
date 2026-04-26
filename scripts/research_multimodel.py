#!/usr/bin/env python3
"""Call Gemini, ChatGPT, or DeepSeek API for a research topic.

Usage: research_multimodel.py <model> <entry_uuid> [gemini_tier]
  model: "gemini", "chatgpt", "deepseek-flash", or "deepseek-pro"
  entry_uuid: UUID of the model-specific research entry (already created)
  gemini_tier: "flex" (default) or "standard" (gemini only)

Loads the entry, builds a prompt from research-system-prompt-v2,
calls the API with web search enabled (or MCP tools for DeepSeek), writes
result to the entry, and checks whether all dispatched models are done to
trigger synthesis.
"""
import asyncio
import os
import sys
import time
import traceback
from contextlib import AsyncExitStack

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.action_runner import (
    build_research_prompt,
    load_reader_context,
    research_model_complete,
)
from tjai_app.models import Entry

import logging

logger = logging.getLogger('research_multimodel')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='research_multimodel')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

API_TIMEOUT = 1800  # 30 minutes
TJAI_MCP_URL = os.environ.get('TJAI_MCP_URL', 'https://etaverse.com/tjai/mcp/')
DEEPSEEK_TOOL_PREFIXES = ('get_', 'list_', 'search_')
DEEPSEEK_TOOL_NAMES = {'get_server_instructions'}
DEEPSEEK_TOOL_DENY = {'run_action'}

DEEPSEEK_WRAPPER_NOTE = """## DeepSeek tjai MCP execution note

You have read-only tjai MCP tools for research context: search, list, and get
tools. Mutating tools are intentionally not exposed in this API subprocess.
Return the completed research report as your final response; the wrapper will
write that final response into the research entry.
"""


async def _alog(level, message, *args, **kwargs):
    """Log from async code without calling Django DB handlers in the event loop."""
    await asyncio.to_thread(logger.log, level, message, *args, **kwargs)


def _deepseek_prompt(prompt):
    return f"{DEEPSEEK_WRAPPER_NOTE}\n\n{prompt}"


def _deepseek_tool_allowed(name):
    if name in DEEPSEEK_TOOL_DENY:
        return False
    return name in DEEPSEEK_TOOL_NAMES or name.startswith(DEEPSEEK_TOOL_PREFIXES)


def _call_gemini(prompt, initial_tier='flex'):
    """Call Gemini API with search grounding.

    First attempt uses `initial_tier` ('flex' by default for scheduled
    runs — 50% cost saving on latency-tolerant background work; 'standard'
    for UI-initiated reruns where the user is waiting). Under high demand
    Flex capacity returns 503 UNAVAILABLE; on that specific error we retry
    at the Standard tier with 5 / 20 / 60 minute backoff. Any non-503 error
    is raised immediately.
    """
    from google import genai
    from google.genai import types

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    client = genai.Client(api_key=api_key)
    grounding_tool = types.Tool(google_search=types.GoogleSearch())

    attempts = [
        (initial_tier, 0),
        ('standard',   5 * 60),
        ('standard',   20 * 60),
        ('standard',   60 * 60),
    ]

    last_err = None
    for idx, (tier, delay) in enumerate(attempts):
        if delay:
            logger.info(
                "Gemini retry %d: waiting %ds before %s-tier attempt",
                idx, delay, tier,
            )
            time.sleep(delay)
        # Dict form so 'service_tier' lands as a request field even if the
        # installed google-genai SDK predates the typed accessor.
        config = {
            'tools': [grounding_tool],
            'service_tier': tier,
            'http_options': {'timeout': API_TIMEOUT * 1000},  # milliseconds
        }
        logger.info(
            "Calling Gemini API (gemini-3.1-pro-preview, %s tier, attempt %d/%d)...",
            tier, idx + 1, len(attempts),
        )
        try:
            response = client.models.generate_content(
                model='gemini-3.1-pro-preview',
                contents=prompt,
                config=config,
            )
            if not response.text:
                raise RuntimeError(f"Gemini returned empty response: {response}")
            return response.text
        except Exception as e:
            msg = str(e)
            if '503' in msg or 'UNAVAILABLE' in msg:
                logger.warning(
                    "Gemini %s-tier attempt %d/%d got 503 UNAVAILABLE: %s",
                    tier, idx + 1, len(attempts), msg[:300],
                )
                last_err = e
                continue
            raise

    raise RuntimeError(
        f"Gemini 503 UNAVAILABLE after {len(attempts)} attempts "
        f"({initial_tier} then standard×3, 5/20/60 min backoff): {last_err}"
    )


def _call_chatgpt(prompt):
    """Call ChatGPT Responses API with web search."""
    from openai import OpenAI

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)

    logger.info("Calling ChatGPT API (gpt-4o)...")
    response = client.responses.create(
        model='gpt-4o',
        input=prompt,
        tools=[{'type': 'web_search'}],
    )

    # Extract text from the response output
    result = response.output_text
    if not result:
        raise RuntimeError(f"ChatGPT returned empty response: {response}")

    return result


def _get_tjai_mcp_token():
    """Return the tjai MCP bearer token from env or SysConfig."""
    token = os.environ.get('TJAI_MCP_TOKEN', '').strip()
    if token:
        return token
    try:
        from tjai_app.models import SysConfig
        return (
            SysConfig.objects.filter(key='mcp_bearer_token')
            .values_list('value', flat=True)
            .first()
            or ''
        ).strip()
    except Exception as e:
        logger.warning("Could not load mcp_bearer_token from SysConfig: %s", e)
        return ''


class TjaiMcpClient:
    """HTTP MCP client that exposes tjai tools to DeepSeek."""

    def __init__(self, url, token):
        self.url = url
        self.token = token
        self._stack = None
        self._session = None
        self.tools = []

    async def start(self):
        if not self.token:
            raise RuntimeError("TJAI_MCP_TOKEN / SysConfig mcp_bearer_token not available")

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        transport = await self._stack.enter_async_context(
            streamablehttp_client(
                self.url,
                headers={'Authorization': f'Bearer {self.token}'},
                timeout=60,
                sse_read_timeout=300,
            )
        )
        read, write, _get_session_id = transport
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        tools_resp = await self._session.list_tools()

        self.tools = []
        for tool in tools_resp.tools:
            if not _deepseek_tool_allowed(tool.name):
                continue
            self.tools.append({
                'name': tool.name,
                'description': tool.description or '',
                'input_schema': tool.inputSchema or {'type': 'object', 'properties': {}},
            })
        await _alog(logging.INFO, "DeepSeek MCP ready: %d tjai tools from %s",
                    len(self.tools), self.url)

    async def call(self, name, arguments):
        if self._session is None:
            return "MCP session is not initialized", True
        try:
            result = await self._session.call_tool(name, arguments=arguments or {})
        except Exception as e:
            return f"tool {name!r} call raised: {type(e).__name__}: {e}", True

        parts = []
        for block in (result.content or []):
            text = getattr(block, 'text', None)
            parts.append(text if text is not None else repr(block))
        return '\n'.join(parts).strip() or '(empty result)', bool(getattr(result, 'isError', False))

    async def close(self):
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except Exception as e:
                await _alog(logging.WARNING, "DeepSeek MCP close warning: %s", e)
        self._stack = None
        self._session = None
        self.tools = []


def _looks_like_unfired_tool_stub(text):
    lowered = (text or '').strip().lower()
    if not lowered:
        return True
    markers = (
        '<tjai_tool_use_control>',
        '<function_result>',
        '"type": "internal_monologue"',
        '"tool": "mcp__',
        '```json\n{\n  "tool":',
        'proceeding with research',
        'stand by for the report',
    )
    return any(marker in lowered for marker in markers)


async def _call_deepseek(prompt, tier):
    """Call DeepSeek V4 via Anthropic-compatible endpoint with tjai MCP tools.

    DeepSeek exposes an Anthropic-compat surface at
    https://api.deepseek.com/anthropic — using the anthropic SDK with
    api_key + base_url overrides keeps this idiomatically Anthropic-shaped
    rather than mixing in an OpenAI-shaped second path.

    Tier maps to the API model string:
      'flash' → deepseek-v4-flash
      'pro'   → deepseek-v4-pro

    The loop mirrors the working codoc-ai DeepSeek runner: expose MCP tools,
    execute tool_use blocks, append tool_result blocks, and return final text.
    """
    from anthropic import Anthropic

    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set in environment")

    api_model = f'deepseek-v4-{tier}'
    client = Anthropic(
        api_key=api_key,
        base_url='https://api.deepseek.com/anthropic',
        timeout=API_TIMEOUT,
    )
    mcp = TjaiMcpClient(TJAI_MCP_URL, _get_tjai_mcp_token())
    await mcp.start()

    messages = [{'role': 'user', 'content': prompt}]
    accumulated_chunks = []

    try:
        iteration = 0
        while True:
            iteration += 1
            await _alog(
                logging.INFO,
                "Calling DeepSeek API (%s, Anthropic-compat, iter %d, %d tools)...",
                api_model, iteration, len(mcp.tools),
            )
            response = await asyncio.to_thread(
                lambda: client.messages.create(
                    model=api_model,
                    max_tokens=384_000,
                    messages=messages,
                    tools=mcp.tools,
                )
            )

            assistant_blocks = []
            for block in response.content or []:
                block_type = getattr(block, 'type', None)
                if block_type == 'text':
                    assistant_blocks.append({'type': 'text', 'text': block.text})
                elif block_type == 'tool_use':
                    assistant_blocks.append({
                        'type': 'tool_use',
                        'id': block.id,
                        'name': block.name,
                        'input': block.input,
                    })
                elif block_type == 'thinking':
                    assistant_blocks.append({
                        'type': 'thinking',
                        'thinking': getattr(block, 'thinking', ''),
                    })
                else:
                    await _alog(logging.WARNING, "DeepSeek: dropping unknown block type %r", block_type)

            messages.append({'role': 'assistant', 'content': assistant_blocks})
            text_this_turn = ''.join(
                b['text'] for b in assistant_blocks if b['type'] == 'text'
            )

            if response.stop_reason == 'tool_use':
                tool_uses = [b for b in assistant_blocks if b['type'] == 'tool_use']
                await _alog(logging.INFO, "DeepSeek requested %d MCP tool call(s)", len(tool_uses))
                tool_results = []
                for tool_use in tool_uses:
                    tool_text, is_error = await mcp.call(
                        tool_use['name'], tool_use['input'] or {}
                    )
                    await _alog(
                        logging.INFO,
                        "DeepSeek tool %s -> %s%d chars",
                        tool_use['name'],
                        'ERROR ' if is_error else '',
                        len(tool_text),
                    )
                    result_block = {
                        'type': 'tool_result',
                        'tool_use_id': tool_use['id'],
                        'content': tool_text,
                    }
                    if is_error:
                        result_block['is_error'] = True
                    tool_results.append(result_block)
                messages.append({'role': 'user', 'content': tool_results})
                continue

            if response.stop_reason == 'max_tokens':
                await _alog(
                    logging.WARNING,
                    "DeepSeek hit max_tokens after %d chars; requesting continuation",
                    len(text_this_turn),
                )
                accumulated_chunks.append(text_this_turn)
                messages.append({
                    'role': 'user',
                    'content': (
                        'Your previous response hit the per-call max_tokens limit. '
                        'Continue from exactly where you left off. Do not repeat or '
                        'summarize earlier text.'
                    ),
                })
                continue

            result = ''.join(accumulated_chunks + [text_this_turn]).strip()
            if not result:
                raise RuntimeError(
                    f"DeepSeek returned no final text. "
                    f"Block types: {[getattr(b, 'type', '?') for b in (response.content or [])]}"
                )
            if _looks_like_unfired_tool_stub(result):
                raise RuntimeError("DeepSeek returned simulated tool/control output, not a report")
            return result
    finally:
        await mcp.close()


def main():
    if len(sys.argv) not in (3, 4):
        print("Usage: research_multimodel.py <model> <entry_uuid> [gemini_tier]",
              file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    entry_uuid = sys.argv[2]
    gemini_tier = sys.argv[3] if len(sys.argv) == 4 else 'flex'

    valid_models = ('gemini', 'chatgpt', 'deepseek-flash', 'deepseek-pro')
    if model not in valid_models:
        logger.error("Invalid model: %s (must be one of: %s)",
                     model, ', '.join(valid_models))
        sys.exit(1)
    if gemini_tier not in ('flex', 'standard'):
        logger.error("Invalid gemini_tier: %s (must be 'flex' or 'standard')",
                     gemini_tier)
        sys.exit(1)

    ref_extra = {'entry_id': entry_uuid, 'action_id': 'research-agent',
                 'model': model}
    start_time = time.time()

    # Load the target entry
    entry = Entry.objects.filter(id=entry_uuid, deleted_at__isnull=True).first()
    if not entry:
        logger.error("Entry %s not found", entry_uuid, extra=ref_extra)
        sys.exit(1)

    topic = entry.content
    logger.info("Starting %s research: %s", model, topic[:100], extra=ref_extra)

    # Mark as active
    entry.status = 'active'
    entry.save(update_fields=['status'])

    # Separate logger for completion events — goes to source='agent_complete'
    # so all agent completions are in one queryable source
    completion_logger = logging.getLogger('agent_complete_multimodel')
    completion_logger.setLevel(logging.INFO)
    if not completion_logger.handlers:
        _cfmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                                   datefmt='%Y-%m-%d %H:%M:%S')
        _cdb = DbLogHandler(source='agent_complete')
        _cdb.setFormatter(_cfmt)
        completion_logger.addHandler(_cdb)

    try:
        # Build the prompt
        reader_context = load_reader_context()
        prompt = build_research_prompt(topic, model, reader_context)

        # Call the appropriate API
        if model == 'gemini':
            result = _call_gemini(prompt, initial_tier=gemini_tier)
        elif model == 'chatgpt':
            result = _call_chatgpt(prompt)
        else:
            # deepseek-flash / deepseek-pro — Anthropic-compat endpoint
            # with tjai MCP tools exposed through a multi-turn tool loop.
            tier = model.split('-', 1)[1]
            result = asyncio.run(_call_deepseek(_deepseek_prompt(prompt), tier))

        # Write result to entry
        # Preserve topic as first line, add report below
        entry.content = f"{topic}\n\n{result}"
        entry.status = 'done'
        entry.save(update_fields=['content', 'status'])

        duration_sec = round(time.time() - start_time)
        ref_extra.update({'run_status': 'completed', 'exit_code': 0,
                          'duration_sec': duration_sec})
        logger.info("%s research complete: %d chars, %ds",
                     model, len(result), duration_sec, extra=ref_extra)
        completion_logger.info("research-agent/%s: exit_code=0, status=completed",
                                model, extra=ref_extra)

        # Update base entry and check if all 3 models are done
        research_model_complete(entry)

    except Exception as e:
        duration_sec = round(time.time() - start_time)
        ref_extra.update({'run_status': 'failed', 'exit_code': 1,
                          'duration_sec': duration_sec})
        error_msg = f"{model} research failed: {e}\n{traceback.format_exc()}"
        logger.error(error_msg, extra=ref_extra)
        completion_logger.info("research-agent/%s: exit_code=1, status=failed",
                                model, extra=ref_extra)
        entry.content = f"{topic}\n\nERROR: {error_msg}"
        entry.status = 'failed'
        entry.save(update_fields=['content', 'status'])

        # Mark the model failed on base and run the terminal-check + synthesis
        # trigger: failed counts as done for synthesis purposes, so a failure
        # that's the last remaining model still fires the synthesis step.
        try:
            research_model_complete(entry, terminal_status='failed')
        except Exception as be:
            logger.error("research_model_complete(failed) failed: %s", be)

        sys.exit(1)


if __name__ == '__main__':
    main()
