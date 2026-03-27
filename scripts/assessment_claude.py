#!/usr/bin/env python3
"""Run Claude assessment for a specific date using dialog injection.

Usage: assessment_claude.py [YYYY-MM-DD]

Same approach as assessment_gemini.py: pre-fetches dialog from DB,
injects into prompt, calls Claude via `claude -p` (subscription, no API cost),
parses structured JSON + markdown output, writes the assessment entry.

If no date argument, defaults to today.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid as uuid_mod

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry, SysConfig, Tag
from normalize_assessment import normalize_entry

import logging

logger = logging.getLogger('assessment_claude')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='assessment_claude')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

CLAUDE_TIMEOUT = 1800  # 30 minutes


# Import shared functions from assessment_gemini (dialog fetch, prompt build, entry write)
from assessment_gemini import fetch_dialog, build_prompt, parse_response, write_entry


def _find_claude():
    """Find the claude CLI binary."""
    path = shutil.which('claude')
    if path:
        return path
    fallback = os.path.expanduser('~/.local/bin/claude')
    if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
        return fallback
    raise RuntimeError("'claude' CLI not found in PATH or ~/.local/bin")


def call_claude(prompt):
    """Call Claude via `claude -p` (subscription auth, no API cost).

    Pipes prompt via stdin to avoid OS argument length limits on large prompts.
    """
    claude_path = _find_claude()

    cmd = [
        claude_path,
        '-p',
        '--output-format', 'text',
        '--model', 'sonnet',
        '--effort', 'medium',
    ]

    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    env.pop('ANTHROPIC_API_KEY', None)  # Force subscription auth
    env['TJAI_ACTION_ID'] = 'llm-assessment'  # Prevent dialog recording

    logger.info("Calling claude -p (sonnet, subscription, %d char prompt via stdin)...", len(prompt))
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT, env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p timed out after {CLAUDE_TIMEOUT}s")

    if result.returncode != 0:
        stderr = result.stderr[:500] if result.stderr else '(no stderr)'
        raise RuntimeError(f"claude -p exited {result.returncode}: {stderr}")

    if not result.stdout.strip():
        raise RuntimeError("claude -p returned empty output")

    return result.stdout


def _write_claude_entry(date_str, scores_data, content):
    """Write the Claude assessment entry (no -gemini suffix)."""
    entry_id = f'assessment-{date_str}'
    now = time.time()

    data = {
        'entry_id': entry_id,
        'date': date_str,
        'scores': scores_data.get('scores', []),
        'total_turns': scores_data.get('total_turns', 0),
        'scored_events': scores_data.get('scored_events', 0),
        'final_cumulative': scores_data.get('final_cumulative', 0),
        'assessor': 'claude',
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
        id=str(uuid_mod.uuid4()),
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


def main():
    from datetime import datetime
    from tjai_app.services import get_timezone
    from assessment_gemini import _set_status, _parse_args

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

        response = call_claude(prompt)
        logger.info("Claude response: %d chars", len(response))

        scores_data, content = parse_response(response)
        logger.info("Parsed: %d scored events", len(scores_data.get('scores', [])))

        _write_claude_entry(date_str, scores_data, content)

        duration = round(time.time() - start_time)
        logger.info("Claude assessment complete for %s in %ds", date_str, duration)
        _set_status(action_id, 'completed')

    except Exception as e:
        duration = round(time.time() - start_time)
        logger.error("Claude assessment failed for %s after %ds: %s\n%s",
                      date_str, duration, e, traceback.format_exc())
        _set_status(action_id, 'failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
