#!/usr/bin/env python3
"""Call Gemini, ChatGPT, or DeepSeek API for a research topic.

Usage: research_multimodel.py <model> <entry_uuid> [gemini_tier]
  model: "gemini", "chatgpt", "deepseek-flash", or "deepseek-pro"
  entry_uuid: UUID of the model-specific research entry (already created)
  gemini_tier: "flex" (default) or "standard" (gemini only)

Loads the entry, builds a prompt from research-system-prompt-v2,
calls the API with web search enabled (or SerpAPI prefetch for DeepSeek,
which has no native grounding), writes result to the entry, and checks
whether all dispatched models are done to trigger synthesis.
"""
import os
import sys
import time
import traceback

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


def _fetch_web_context(query):
    """Fetch SerpAPI organic results as markdown context.

    Best-effort enrichment for models without native web grounding (DeepSeek).
    Returns formatted markdown, or '' if SERPAPI_API_KEY is unset or the
    request fails. Never raises — web context is enrichment, not a blocker.

    No client-side caps: `num` is not sent so SerpAPI's own default applies,
    and ALL returned organic results are formatted into the context (no
    slicing). The query is passed through unmodified.
    """
    api_key = os.environ.get('SERPAPI_API_KEY')
    if not api_key:
        logger.info("SERPAPI_API_KEY not set; running without web context")
        return ''

    import json as _json
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({
        'engine': 'google',
        'q': query,
        'api_key': api_key,
    })
    url = f'https://serpapi.com/search.json?{params}'
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning("SerpAPI fetch failed for %r: %s", query, e)
        return ''

    if 'error' in data:
        logger.warning("SerpAPI returned error for %r: %s", query, data['error'])
        return ''

    organic = data.get('organic_results') or []
    if not organic:
        return ''

    lines = [f'Recent web search results for: {query}', '']
    for i, it in enumerate(organic, start=1):
        title = it.get('title', '(no title)')
        link = it.get('link', '')
        snippet = (it.get('snippet') or '').replace('\n', ' ').strip()
        lines.append(f'{i}. **{title}**')
        if link:
            lines.append(f'   {link}')
        if snippet:
            lines.append(f'   {snippet}')
        lines.append('')
    return '\n'.join(lines).rstrip()


def _call_deepseek(prompt, tier):
    """Call DeepSeek V4 via its Anthropic-compatible endpoint.

    DeepSeek exposes an Anthropic-compat surface at
    https://api.deepseek.com/anthropic — using the anthropic SDK with
    api_key + base_url overrides keeps this idiomatically Anthropic-shaped
    rather than mixing in an OpenAI-shaped second path.

    Tier maps to the API model string:
      'flash' → deepseek-v4-flash
      'pro'   → deepseek-v4-pro

    DeepSeek has no native web-search tool; the caller injects SerpAPI
    context via _fetch_web_context before calling.
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

    logger.info("Calling DeepSeek API (%s, Anthropic-compat)...", api_model)
    # max_tokens is REQUIRED by DeepSeek's Anthropic-compat endpoint
    # (HTTP 400 if absent). 384_000 is DeepSeek V4's own documented model
    # max (per api-docs.deepseek.com/quick_start/pricing — same for both
    # flash and pro), so the only ceiling here is the model's own spec.
    response = client.messages.create(
        model=api_model,
        max_tokens=384_000,
        messages=[{'role': 'user', 'content': prompt}],
    )

    # DeepSeek V4 returns extended-thinking blocks before the text block;
    # concatenate all text blocks, ignoring thinking.
    text_parts = [
        getattr(b, 'text', '') for b in (response.content or [])
        if getattr(b, 'type', None) == 'text'
    ]
    result = ''.join(text_parts).strip()
    if not result:
        raise RuntimeError(
            f"DeepSeek returned no text blocks. "
            f"Block types: {[getattr(b, 'type', '?') for b in (response.content or [])]}"
        )
    return result


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
            # deepseek-flash / deepseek-pro — no native web search, so
            # prepend a SerpAPI prefetch as a "Recent web search results"
            # block. Search query is the topic's first line, capped.
            tier = model.split('-', 1)[1]
            # Use the topic's first line as the search query, untruncated.
            # No length cap — SerpAPI / Google enforce their own URL limits
            # if the query is genuinely too long.
            search_query = topic.split('\n', 1)[0].strip()
            web_context = _fetch_web_context(search_query)
            full_prompt = (
                f"## Recent web search results (SerpAPI Google)\n\n"
                f"{web_context}\n\n---\n\n{prompt}"
            ) if web_context else prompt
            result = _call_deepseek(full_prompt, tier)

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
