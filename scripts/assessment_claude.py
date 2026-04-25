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
from pathlib import Path

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

CLAUDE_TIMEOUT = 3600  # 1 hour


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


def _nohook_home():
    """Build (or refresh) a scratch HOME that mirrors the real one but with
    all Claude Code hooks disabled. Returns the scratch HOME path.

    Needed because ~/.claude/settings.json wires a stop-phrase-guard Stop hook
    that grep-matches phrases like "pre-existing" in model context. The
    assessment prompt injects 1000+ dialog turns containing exactly such
    phrases (from the dialog being scored, not from the scoring model's own
    output), so the hook fires, Claude replies to the hook feedback instead
    of the scoring task, the ```json``` block never appears, and
    assessment_gemini.parse_response raises. Saw this cold on both the
    original 01:00 ET run and the manual 10:24 ET rerun on 2026-04-22.

    Claude Code offers no documented way to disable hooks per-invocation
    (verified via claude-code-guide agent, 2026-04-22). Its only lever is
    the settings.json key `"disableAllHooks": true`, read from
    `$HOME/.claude/settings.json`. So we HOME-redirect: mirror ~/.claude/
    via symlinks, drop a settings.json with disableAllHooks=true and no
    hooks block, and symlink ~/.claude.json (which Claude Code also needs).
    Fresh rebuild each call so any user-side settings.json changes flow
    through automatically (we read the real one and splice).
    """
    scratch = Path('/var/www/tjai/data/claude-nohook-home')
    scratch_claude = scratch / '.claude'
    real_claude = Path.home() / '.claude'
    real_claude_json = Path.home() / '.claude.json'

    # Clear old symlinks + our written settings.json, then rebuild.
    if scratch_claude.exists():
        for item in scratch_claude.iterdir():
            if item.is_symlink():
                item.unlink()
            elif item.name == 'settings.json' and item.is_file():
                item.unlink()
    scratch_claude.mkdir(parents=True, exist_ok=True)

    # Symlink every entry in real ~/.claude/ except settings.json
    for item in real_claude.iterdir():
        if item.name == 'settings.json':
            continue
        (scratch_claude / item.name).symlink_to(item)

    # Symlink ~/.claude.json at HOME root — Claude looks for it there too
    cjson_link = scratch / '.claude.json'
    if cjson_link.is_symlink():
        cjson_link.unlink()
    elif cjson_link.exists():
        cjson_link.unlink()
    cjson_link.symlink_to(real_claude_json)

    # Write the override settings.json: real config minus hooks, plus disable flag
    with open(real_claude / 'settings.json') as f:
        settings = json.load(f)
    settings['disableAllHooks'] = True
    settings.pop('hooks', None)
    with open(scratch_claude / 'settings.json', 'w') as f:
        json.dump(settings, f, indent=2)

    return str(scratch)


def call_claude(prompt):
    """Call Claude via `claude -p` (subscription auth, no API cost).

    Pipes prompt via stdin to avoid OS argument length limits on large prompts.
    """
    claude_path = _find_claude()

    cmd = [
        claude_path,
        '-p',
        '--output-format', 'text',
        '--model', 'opus',
        '--effort', 'xhigh',
    ]

    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    env.pop('ANTHROPIC_API_KEY', None)  # Force subscription auth
    env['TJAI_ACTION_ID'] = 'llm-assessment'  # Prevent dialog recording
    env['HOME'] = _nohook_home()  # disableAllHooks=true settings override

    logger.info("Calling claude -p (opus, subscription, %d char prompt via stdin)...", len(prompt))
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

        # Save raw response for debugging parse failures
        from pathlib import Path
        response_dir = Path(__file__).resolve().parent.parent / 'data' / 'assessment-responses'
        response_dir.mkdir(parents=True, exist_ok=True)
        response_file = response_dir / f'{date_str}-claude.txt'
        response_file.write_text(response, encoding='utf-8')
        logger.info("Saved raw response to %s", response_file)

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
