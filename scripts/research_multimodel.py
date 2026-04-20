#!/usr/bin/env python3
"""Call Gemini or ChatGPT API for a research topic.

Usage: research_multimodel.py <model> <entry_uuid>
  model: "gemini" or "chatgpt"
  entry_uuid: UUID of the model-specific research entry (already created)

Loads the entry, builds a prompt from research-system-prompt-v2,
calls the API with web search enabled, writes result to the entry,
and checks whether all three models are done to trigger synthesis.
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


def main():
    if len(sys.argv) not in (3, 4):
        print("Usage: research_multimodel.py <model> <entry_uuid> [gemini_tier]",
              file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    entry_uuid = sys.argv[2]
    gemini_tier = sys.argv[3] if len(sys.argv) == 4 else 'flex'

    if model not in ('gemini', 'chatgpt'):
        logger.error("Invalid model: %s (must be 'gemini' or 'chatgpt')", model)
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
        else:
            result = _call_chatgpt(prompt)

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
            from tjai_app.action_runner import research_model_complete
            research_model_complete(entry, terminal_status='failed')
        except Exception as be:
            logger.error("research_model_complete(failed) failed: %s", be)

        sys.exit(1)


if __name__ == '__main__':
    main()
