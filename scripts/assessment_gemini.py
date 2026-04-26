#!/usr/bin/env python3
"""Run Gemini assessment for a specific date.

Usage: assessment_gemini.py [YYYY-MM-DD]

Fetches dialog data from DB, sends to Gemini API with the assessment
system prompt (adapted for non-MCP use), parses structured scores,
and writes the assessment entry.

If no date argument, defaults to today (matching Claude assessment behavior).
"""
import json
import os
import re
import sys
import time
import traceback
import uuid as uuid_mod
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry, SysConfig, Tag
from normalize_assessment import normalize_entry

import logging

logger = logging.getLogger('assessment_gemini')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='assessment_gemini')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

API_TIMEOUT = 600  # 10 minutes


def fetch_dialog(date_str):
    """Fetch dialog turns for a specific date, ordered chronologically."""
    from datetime import datetime, timedelta
    from django.utils.timezone import make_aware
    from tjai_app.services import get_timezone

    tz = get_timezone()
    target = datetime.strptime(date_str, '%Y-%m-%d').date()
    next_day = target + timedelta(days=1)

    start_ts = make_aware(datetime.combine(target, datetime.min.time()), tz).timestamp()
    end_ts = make_aware(datetime.combine(next_day, datetime.min.time()), tz).timestamp()

    dialog_ids = Tag.objects.filter(tag_name='ccdialog').values_list('entry_id', flat=True)

    entries = Entry.objects.filter(
        id__in=dialog_ids,
        deleted_at__isnull=True,
        timestamp_created__gte=start_ts,
        timestamp_created__lt=end_ts,
    ).order_by('timestamp_created')

    turns = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        ts = datetime.fromtimestamp(e.timestamp_created, tz).isoformat()
        turns.append({
            'timestamp': ts,
            'role': data.get('role', 'unknown'),
            'client': data.get('client', ''),
            'model': data.get('model', ''),
            'hostname': data.get('hostname', ''),
            'content': e.content,
        })

    return turns


def build_prompt(date_str, dialog_turns):
    """Build the assessment prompt with dialog data pre-injected."""
    from datetime import datetime, timedelta

    # Load the assessment system prompt
    sp_entry = Entry.objects.filter(
        data__entry_id='assessment-system-prompt',
        deleted_at__isnull=True,
    ).first()
    if not sp_entry:
        raise RuntimeError("assessment-system-prompt entry not found in DB")

    prompt = sp_entry.content

    # Resolve date template variables
    target = datetime.strptime(date_str, '%Y-%m-%d').date()
    next_day = target + timedelta(days=1)
    prompt = prompt.replace('{yyyy-mm-dd}', date_str)
    prompt = prompt.replace('{next-day-yyyy-mm-dd}', next_day.isoformat())

    # Remove MCP-specific instructions: steps 1-2 (fetch + filter dialog)
    prompt = re.sub(
        r'1\. Call get_memories.*?(?=3\. Walk through)',
        '',
        prompt,
        flags=re.DOTALL,
    )

    # Remove output section about create_entry/edit_entry — we handle persistence
    prompt = re.sub(
        r'## Output\n\nCheck if entry_id.*?(?=## Rigor)',
        '',
        prompt,
        flags=re.DOTALL,
    )

    # Format dialog turns as text block
    dialog_lines = []
    for turn in dialog_turns:
        role = turn['role'].upper()
        host = f" [{turn['hostname']}]" if turn['hostname'] else ''
        model = ''
        if turn.get('client') or turn.get('model'):
            model_parts = [p for p in (turn.get('client'), turn.get('model')) if p]
            model = f" ({' / '.join(model_parts)})"
        dialog_lines.append(f"### {turn['timestamp']} {role}{host}{model}\n{turn['content']}")
    dialog_text = '\n\n'.join(dialog_lines)

    full_prompt = f"""{prompt}

## Dialog Data

The following {len(dialog_turns)} dialog turns have been pre-fetched for {date_str}.
Analyze them according to the scoring rules above.

{dialog_text}

## Output Format

Return your assessment in TWO parts:

1. A JSON block delimited by ```json and ``` containing exactly these fields:
   - "scores": array of scored events, each with "dt" (ISO 8601 datetime), "score" (integer), "cumulative" (running total integer), "precis" (30-60 char technical description)
   - "total_turns": {len(dialog_turns)}
   - "scored_events": count of items in scores array
   - "final_cumulative": last cumulative value
   - "date": "{date_str}"

2. After the JSON block, the full assessment in markdown:
   - "# AI Assessment: {date_str}" header
   - "## Summary" section — total turns, scored events, final cumulative, 2-3 sentence assessment
   - "## Assessment Log" section — turn-by-turn analysis with dialog references
   - "## Observations" section — patterns, failure modes, strengths
"""
    return full_prompt


def call_gemini(prompt):
    """Call Gemini API."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig()
    config.http_options = {'timeout': API_TIMEOUT * 1000}  # milliseconds

    logger.info("Calling Gemini API (gemini-2.5-pro)...")
    response = client.models.generate_content(
        model='gemini-2.5-pro',
        contents=prompt,
        config=config,
    )

    if not response.text:
        raise RuntimeError(f"Gemini returned empty response: {response}")

    return response.text


def _clean_json(raw):
    """Fix common LLM JSON errors: trailing commas, comments, truncation."""
    # Remove single-line comments (// ...)
    s = re.sub(r'//[^\n]*', '', raw)
    # Remove trailing commas before } or ]
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # Try parsing as-is first
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Truncated JSON — try closing open structures
    for fix in ('}', ']}', '"]}', '"}]}', '""]}'):
        try:
            return json.loads(s + fix)
        except json.JSONDecodeError:
            continue
    # Last resort: find the largest parseable prefix
    for end in range(len(s), 0, -100):
        for fix in ('', '}', ']}', '"]}', '"}]}'):
            try:
                return json.loads(s[:end] + fix)
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("Could not repair JSON", s, 0)


def parse_response(response_text):
    """Parse LLM response to extract scores JSON and markdown content."""
    # Extract JSON block
    json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
    if not json_match:
        raise RuntimeError("No ```json``` block found in response")

    scores_data = _clean_json(json_match.group(1))

    # Extract markdown content — everything from the first # header after JSON
    after_json = response_text[json_match.end():]
    header_match = re.search(r'^#\s', after_json, re.MULTILINE)
    if header_match:
        content = after_json[header_match.start():].strip()
    else:
        content = after_json.strip()

    return scores_data, content


def write_entry(date_str, scores_data, content):
    """Write or update the Gemini assessment entry."""
    entry_id = f'assessment-{date_str}-gemini'
    now = time.time()

    data = {
        'entry_id': entry_id,
        'date': date_str,
        'scores': scores_data.get('scores', []),
        'total_turns': scores_data.get('total_turns', 0),
        'scored_events': scores_data.get('scored_events', 0),
        'final_cumulative': scores_data.get('final_cumulative', 0),
        'assessor': 'gemini',
    }

    existing = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()

    if existing:
        existing.content = content
        existing.data = data
        existing.timestamp_modified = now
        existing.save(update_fields=['content', 'data', 'timestamp_modified'])
        logger.info("Updated existing entry: %s", entry_id)
        normalize_entry(existing)
        return existing

    entry = Entry.objects.create(
        id=str(uuid_mod.uuid7()),
        content=content,
        kind='memory',
        context_id='tjai',
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
        data=data,
    )
    Tag.objects.create(tag_name='assessment', entry=entry)
    Tag.objects.create(tag_name='fromai', entry=entry)
    logger.info("Created new entry: %s", entry_id)
    normalize_entry(entry)
    return entry


def _set_status(action_id, status):
    """Update agent status in DB. Called by the script itself — no daemon thread."""
    if not action_id:
        return
    now = time.time()
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_status',
        defaults={'value': status, 'timestamp_modified': now})
    # Wake the action agent so it launches the next backfill immediately
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})


def _parse_args():
    """Parse CLI args: date_str and optional --action-id."""
    date_str = None
    action_id = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--action-id' and i + 1 < len(args):
            action_id = args[i + 1]
            i += 2
        elif not date_str:
            date_str = args[i]
            i += 1
        else:
            i += 1
    return date_str, action_id


def main():
    from datetime import datetime
    from tjai_app.services import get_timezone

    date_str, action_id = _parse_args()
    if not date_str:
        logger.error("No date argument provided — date is required")
        _set_status(action_id, 'failed')
        sys.exit(1)

    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        logger.error("Invalid date format: %s (expected YYYY-MM-DD)", date_str)
        _set_status(action_id, 'failed')
        sys.exit(1)

    start_time = time.time()

    try:
        dialog_turns = fetch_dialog(date_str)
        if not dialog_turns:
            logger.info("No dialog found for %s, skipping", date_str)
            _set_status(action_id, 'completed')
            return

        logger.info("Fetched %d dialog turns for %s", len(dialog_turns), date_str)

        prompt = build_prompt(date_str, dialog_turns)
        logger.info("Prompt: %d chars", len(prompt))

        response = call_gemini(prompt)
        logger.info("Gemini response: %d chars", len(response))

        # Save raw response for debugging parse failures
        response_dir = Path(__file__).resolve().parent.parent / 'data' / 'assessment-responses'
        response_dir.mkdir(parents=True, exist_ok=True)
        response_file = response_dir / f'{date_str}-gemini.txt'
        response_file.write_text(response, encoding='utf-8')
        logger.info("Saved raw response to %s", response_file)

        scores_data, content = parse_response(response)
        logger.info("Parsed: %d scored events", len(scores_data.get('scores', [])))

        write_entry(date_str, scores_data, content)

        duration = round(time.time() - start_time)
        logger.info("Gemini assessment complete for %s in %ds", date_str, duration)
        _set_status(action_id, 'completed')

    except Exception as e:
        duration = round(time.time() - start_time)
        logger.error("Gemini assessment failed for %s after %ds: %s\n%s",
                      date_str, duration, e, traceback.format_exc())
        _set_status(action_id, 'failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
