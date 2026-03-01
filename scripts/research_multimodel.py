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
from tjai_app.models import Entry, SysConfig, Tag

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

API_TIMEOUT = 600  # 10 minutes


def _load_reader_context():
    """Load reader profile and AI guidance from DB, return as inline text."""
    parts = []

    # Profile entries
    profiles = Entry.objects.filter(
        kind='profile', deleted_at__isnull=True,
    ).order_by('-timestamp_modified')
    if profiles:
        parts.append("## Reader Profile")
        for p in profiles:
            parts.append(p.content)

    # AI guidance (general only — no context filter)
    guidance = Entry.objects.filter(
        kind='ai', deleted_at__isnull=True, context__isnull=True,
    ).order_by('-timestamp_modified')
    if guidance:
        parts.append("\n## AI Guidance")
        for g in guidance:
            parts.append(g.content)

    return '\n\n'.join(parts)


def _build_prompt(topic, reader_context):
    """Build the research prompt from research-system-prompt-v2."""
    sp_entry = Entry.objects.filter(
        data__entry_id='research-system-prompt-v2',
        deleted_at__isnull=True,
    ).first()
    if not sp_entry:
        raise RuntimeError("research-system-prompt-v2 entry not found in DB")

    prompt = sp_entry.content

    # Remove MCP-specific sections that don't apply to API-only models
    remove_sections = [
        '## Operational',
        '## Entry Provenance',
        '## ALL Content Must Be in tjai Entries',
    ]
    for section in remove_sections:
        idx = prompt.find(section)
        if idx == -1:
            continue
        # Find the next ## heading or end of string
        next_heading = prompt.find('\n## ', idx + len(section))
        if next_heading == -1:
            prompt = prompt[:idx].rstrip()
        else:
            prompt = prompt[:idx] + prompt[next_heading:]

    # Also remove individual MCP references in the reader profile section
    for phrase in [
        'Call get_profile() and get_ai_guidance() first.',
        'You can see the MCP interface\nyou have available to further educate and equip yourself for this task.',
        'You can see the MCP interface you have available to further educate and equip yourself for this task.',
    ]:
        prompt = prompt.replace(phrase, '')

    # Remove references to tjai MCP tools
    for phrase in [
        '- Use MCP tools for tjai data access (search_entries, edit_entry).\n',
        '- Write the completed report directly into the research entry via edit_entry,\n  setting status to "done".\n',
    ]:
        prompt = prompt.replace(phrase, '')

    # Build the full prompt
    full_prompt = f"""{reader_context}

{prompt}

## Research Topic

{topic}

## Output Instructions

Return your complete research report as your response. Use web search
extensively to find current, authoritative information. Structure the report
exactly as specified in the Output Format section above."""

    return full_prompt


def _call_gemini(prompt):
    """Call Gemini API with search grounding."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    client = genai.Client(api_key=api_key)

    grounding_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[grounding_tool])

    logger.info("Calling Gemini API (gemini-2.5-pro)...")
    response = client.models.generate_content(
        model='gemini-2.5-pro',
        contents=prompt,
        config=config,
    )

    if not response.text:
        raise RuntimeError(f"Gemini returned empty response: {response}")

    return response.text


def _call_chatgpt(prompt):
    """Call ChatGPT Responses API with web search."""
    from openai import OpenAI

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key)

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


def _check_and_trigger_synthesis(entry):
    """Check if all 3 models are done and trigger synthesis if so."""
    data = entry.data if isinstance(entry.data, dict) else {}
    base_entry_id = data.get('base_entry_id')
    if not base_entry_id:
        logger.warning("No base_entry_id in entry data, cannot check synthesis")
        return

    # Look up all three entries
    base = Entry.objects.filter(
        data__entry_id=base_entry_id, deleted_at__isnull=True,
    ).first()
    gemini = Entry.objects.filter(
        data__entry_id=f'{base_entry_id}-gemini', deleted_at__isnull=True,
    ).first()
    chatgpt = Entry.objects.filter(
        data__entry_id=f'{base_entry_id}-chatgpt', deleted_at__isnull=True,
    ).first()

    if not all([base, gemini, chatgpt]):
        logger.info("Not all model entries exist yet, skipping synthesis check")
        return

    if not all(e.status == 'done' for e in [base, gemini, chatgpt]):
        logger.info("Not all models done yet (base=%s, gemini=%s, chatgpt=%s)",
                     base.status, gemini.status, chatgpt.status)
        return

    # Check if synthesis already exists
    synth_entry_id = f'{base_entry_id}-synthesis'
    existing = Entry.objects.filter(
        data__entry_id=synth_entry_id, deleted_at__isnull=True,
    ).first()
    if existing:
        logger.info("Synthesis entry %s already exists, skipping", synth_entry_id)
        return

    logger.info("All 3 models done! Creating synthesis entry: %s", synth_entry_id)
    _create_and_dispatch_synthesis(base_entry_id, base, synth_entry_id)


def _create_and_dispatch_synthesis(base_entry_id, base_entry, synth_entry_id):
    """Create synthesis entry and dispatch Claude to run it."""
    now = time.time()

    # Create synthesis entry
    synth = Entry.objects.create(
        content=f"Synthesis: {base_entry.content.split(chr(10))[0][:200]}",
        kind='memory',
        context=base_entry.context,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
        data={
            'entry_id': synth_entry_id,
            'source': 'multimodel',
            'base_entry_id': base_entry_id,
            'base_uuid': str(base_entry.id),
            'model': 'synthesis',
        },
    )
    Tag.objects.create(tag_name='research', entry=synth)

    # Load synthesis prompt template
    sp_entry = Entry.objects.filter(
        data__entry_id='research-synthesis-prompt',
        deleted_at__isnull=True,
    ).first()
    if not sp_entry:
        logger.error("research-synthesis-prompt entry not found — cannot dispatch synthesis")
        return

    # Substitute {research_entry_id} in the prompt
    synthesis_prompt = sp_entry.content.replace('{research_entry_id}', base_entry_id)

    # Dispatch via research-agent action (reuse existing mechanism)
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not research_action:
        logger.error("research-agent action entry not found — cannot dispatch synthesis")
        return

    data = research_action.data or {}
    data['last_run'] = 0
    data['next_target'] = (
        f"SPECIFIC TARGET:\nEntry UUID: {synth.id}\n"
        f"SYNTHESIS TASK — use the following prompt instead of normal research:\n\n"
        f"{synthesis_prompt}"
    )
    data['next_target_entry_id'] = str(synth.id)
    research_action.data = data
    research_action.timestamp_modified = now
    research_action.save(update_fields=['data', 'timestamp_modified'])

    # Wake action agent
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})

    logger.info("Synthesis dispatched: %s (entry %s)", synth_entry_id, synth.id)


def main():
    if len(sys.argv) != 3:
        print("Usage: research_multimodel.py <model> <entry_uuid>", file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    entry_uuid = sys.argv[2]

    if model not in ('gemini', 'chatgpt'):
        logger.error("Invalid model: %s (must be 'gemini' or 'chatgpt')", model)
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
        reader_context = _load_reader_context()
        prompt = _build_prompt(topic, reader_context)

        # Call the appropriate API
        if model == 'gemini':
            result = _call_gemini(prompt)
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

        # Check if synthesis is ready
        _check_and_trigger_synthesis(entry)

    except Exception as e:
        duration_sec = round(time.time() - start_time)
        ref_extra.update({'run_status': 'failed', 'exit_code': 1,
                          'duration_sec': duration_sec})
        error_msg = f"{model} research failed: {e}\n{traceback.format_exc()}"
        logger.error(error_msg, extra=ref_extra)
        completion_logger.info("research-agent/%s: exit_code=1, status=failed",
                                model, extra=ref_extra)
        entry.content = f"{topic}\n\nERROR: {error_msg}"
        entry.status = 'blocked'
        entry.save(update_fields=['content', 'status'])
        sys.exit(1)


if __name__ == '__main__':
    main()
