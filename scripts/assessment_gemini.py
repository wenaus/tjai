#!/usr/bin/env python3
"""Run the daily AI performance assessment for a date.

Usage: assessment_gemini.py YYYY-MM-DD [--plan] [--from-saved] [--no-write]
                            [--action-id ID]

The day's dialog is split by session and cleaned (dialog_prep), then
packed into calls no larger than the call cap: a session over the cap gets
a call of its own, the rest pack together up to it, and a session is never
split. Each call receives the assessment system prompt and its sessions'
turns and returns scores and a log for those turns alone. The calls are
merged in code, scores in time order with the cumulative recomputed, and
one small call writes the day's Summary and Observations from the merged
events and each part's observations. One entry per day, the same shape as
a single-call assessment. Design: docs/assessment.md.

--plan        print the packs and stop; no API call
--from-saved  re-parse the saved raw responses for the date instead of
              calling the API, for recovering a day whose responses
              arrived whole but failed to parse
--no-write    run everything but the entry write
"""
import json
import os
import shutil
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
from dialog_prep import (ASSESSED_KINDS, CHARS_PER_TOKEN, fetch_dialog, format_turn,
                         pack_calls, pack_turns, split_sessions)

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
MODEL = 'gemini-2.5-pro'

# Comparison runs override these from the command line. The defaults are
# the nightly assessor, so an unflagged run is unchanged.
PROVIDER = 'claude'          # 'claude' or 'codex' (subscriptions), 'gemini' (API)
VARIANT = 'opus'             # names the raw-response and plan files
CODEX_MODEL = 'gpt-5.6-sol'
CODEX_EFFORT = 'high'
# Codex at high effort spends far longer on a 150K-token pack than the
# Gemini API takes to answer one, so the timeout that suits Gemini cuts
# sol off mid-read.
CODEX_TIMEOUT = 3600
CLAUDE_MODEL = 'opus'
CLAUDE_EFFORT = 'xhigh'
CLAUDE_TIMEOUT = 3600

# The largest input for one assessment call, in estimated tokens
# (chars / CHARS_PER_TOKEN). Held in sysconfig so it is editable without a
# deploy; the value comes from the yield-against-size record in
# docs/assessment.md.
CALL_CAP_KEY = 'assessment_call_token_cap'
CALL_CAP_DEFAULT = 100000
CALL_CAP_DESCRIPTION = (
    'Largest input, in estimated tokens (chars/2.7), for one assessment call. '
    'A session over it gets a call of its own; smaller sessions pack together up to it. '
    'Set from the yield-against-size record (docs/assessment.md).')


def call_token_cap():
    row = SysConfig.objects.filter(key=CALL_CAP_KEY).first()
    if row is None:
        SysConfig.objects.create(key=CALL_CAP_KEY, value=str(CALL_CAP_DEFAULT),
                                 description=CALL_CAP_DESCRIPTION, timestamp_modified=time.time())
        return CALL_CAP_DEFAULT
    try:
        return int(row.value)
    except ValueError:
        logger.error("sysconfig %s is not an integer (%r); using %d", CALL_CAP_KEY, row.value, CALL_CAP_DEFAULT)
        return CALL_CAP_DEFAULT


def _system_prompt(date_str):
    """The assessment system prompt, with the MCP fetch steps and the
    persistence section removed since this script does both."""
    from datetime import datetime, timedelta

    sp_entry = Entry.objects.filter(
        data__entry_id='assessment-system-prompt',
        deleted_at__isnull=True,
    ).first()
    if not sp_entry:
        raise RuntimeError("assessment-system-prompt entry not found in DB")

    prompt = sp_entry.content
    target = datetime.strptime(date_str, '%Y-%m-%d').date()
    next_day = target + timedelta(days=1)
    prompt = prompt.replace('{yyyy-mm-dd}', date_str)
    prompt = prompt.replace('{next-day-yyyy-mm-dd}', next_day.isoformat())
    prompt = re.sub(r'1\. Call get_memories.*?(?=3\. Walk through)', '', prompt, flags=re.DOTALL)
    prompt = re.sub(r'## Output\n\nCheck if entry_id.*?(?=## Rigor)', '', prompt, flags=re.DOTALL)
    return prompt


def build_prompt(date_str, dialog_turns, part_note=''):
    """The prompt for one call: the rules, a note on which part of the day
    this is, the turns, and the output format."""
    prompt = _system_prompt(date_str)
    dialog_text = '\n\n'.join(format_turn(t) for t in dialog_turns)
    note = f"\n{part_note}\n" if part_note else ''
    return f"""{prompt}

## Dialog Data

The following {len(dialog_turns)} dialog turns have been pre-fetched for {date_str}.
Analyze them according to the scoring rules above.
{note}
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


def _part_note(i, n, pack):
    lines = [f"This call holds part {i} of {n} of the day's dialog: "
             f"{len(pack)} session{'s' if len(pack) != 1 else ''}."]
    for s in pack:
        lines.append(f"- {s['label']}")
    lines.append("The day's other sessions are assessed in separate calls and merged afterwards, "
                 "so score only what is here. Each session is an independent stream, presented "
                 "whole and in time order; a task never spans two sessions. Each turn's header "
                 "names its host and session and carries the entry UUID to cite.")
    return '\n'.join(lines)


def call_gemini(prompt):
    from google import genai
    from google.genai import types

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig()
    config.http_options = {'timeout': API_TIMEOUT * 1000}  # milliseconds

    logger.info("Calling Gemini API (%s), prompt %d chars...", MODEL, len(prompt))
    response = client.models.generate_content(model=MODEL, contents=prompt, config=config)
    if not response.text:
        raise RuntimeError(f"Gemini returned empty response: {response}")
    return response.text


def call_codex(prompt):
    """Run one assessment call through the Codex subscription.

    Same contract as call_gemini: prompt in, response text out. The prompt
    goes on stdin and the answer comes back through -o, so neither is
    bounded by an argv limit. No MCP, no web search, no repo access: the
    assessor gets the dialog and the system prompt and nothing else, which
    is what the Gemini path gives it.
    """
    import signal
    import subprocess
    import tempfile
    from tj.commands.ai_agent import _find_codex

    with tempfile.TemporaryDirectory(prefix='tjai-assess-codex-') as out_dir:
        out_file = Path(out_dir) / 'assessment.md'
        cmd = [
            _find_codex(),
            '--ask-for-approval', 'never',
            'exec',
            '--ephemeral',
            '--skip-git-repo-check',
            '--sandbox', 'read-only',
            '-m', CODEX_MODEL,
            '-c', f'model_reasoning_effort="{CODEX_EFFORT}"',
            '-o', str(out_file),
            '-',
        ]
        env = os.environ.copy()
        env['HOME'] = os.environ.get('HOME', '/home/admin')
        env['PATH'] = ':'.join([
            '/home/admin/.nvm/versions/node/v24.13.1/bin',
            '/home/admin/.local/bin',
            env.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
        ])
        # Force subscription auth: an API key in the environment would bill.
        env.pop('OPENAI_API_KEY', None)
        env.pop('CODEX_API_KEY', None)
        # An assessment call is not dialog. Without this the Codex record
        # hook writes each call's prompt back into the day's dialog, and the
        # next assessment reads its own prompts: the record then carries the
        # previous day's inside the current one, without bound.
        env['TJAI_DIALOG_TURNS'] = '0'

        logger.info("Calling Codex (%s, effort=%s), prompt %d chars...",
                    CODEX_MODEL, CODEX_EFFORT, len(prompt))
        started = time.monotonic()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=out_dir, env=env,
                                start_new_session=True)
        try:
            out, err = proc.communicate(input=prompt, timeout=CODEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            raise
        proc = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        duration = round(time.monotonic() - started)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or '')[-2000:]
            raise RuntimeError(
                f"codex exited {proc.returncode} after {duration}s: {tail}")
        if not out_file.exists():
            tail = (proc.stderr or proc.stdout or '')[-2000:]
            raise RuntimeError(f"codex wrote no output file: {tail}")
        response = out_file.read_text(encoding='utf-8')
        if not response.strip():
            raise RuntimeError("codex returned an empty response")
        logger.info("Codex returned %d chars in %ds", len(response), duration)
        return response


def call_claude(prompt):
    """Run one assessment call through the Claude subscription.

    Same contract as call_codex: prompt on stdin, response text out. No
    tools, no MCP, no session: the assessor gets the dialog and the system
    prompt and nothing else. A -p run records no dialog (the record hook
    skips print mode), so the call does not read itself back the next day.
    """
    import signal
    import subprocess

    claude_path = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
    if not os.access(claude_path, os.X_OK):
        raise RuntimeError("claude CLI not found in PATH or ~/.local/bin")
    cmd = [
        claude_path,
        '-p',
        '--output-format', 'text',
        '--model', CLAUDE_MODEL,
        '--effort', CLAUDE_EFFORT,
        '--tools', '',
        '--strict-mcp-config',
        '--no-session-persistence',
    ]
    env = os.environ.copy()
    env['HOME'] = os.environ.get('HOME', '/home/admin')
    env['PATH'] = ':'.join([
        '/home/admin/.nvm/versions/node/v24.13.1/bin',
        '/home/admin/.local/bin',
        env.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
    ])
    env.pop('CLAUDECODE', None)
    env.pop('ANTHROPIC_API_KEY', None)  # subscription auth, never the API
    env['TJAI_DIALOG_TURNS'] = '0'

    logger.info("Calling Claude (%s, effort=%s), prompt %d chars...",
                CLAUDE_MODEL, CLAUDE_EFFORT, len(prompt))
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    try:
        out, err = proc.communicate(input=prompt, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    duration = round(time.monotonic() - started)
    if proc.returncode != 0:
        tail = (err or out or '')[-2000:]
        raise RuntimeError(
            f"claude exited {proc.returncode} after {duration}s: {tail}")
    if not out.strip():
        raise RuntimeError("claude returned an empty response")
    logger.info("Claude returned %d chars in %ds", len(out), duration)
    return out


def _call_model(prompt):
    """Dispatch one call to the configured provider."""
    if PROVIDER == 'claude':
        return call_claude(prompt)
    if PROVIDER == 'codex':
        return call_codex(prompt)
    if PROVIDER == 'gemini':
        return call_gemini(prompt)
    raise RuntimeError(f"unknown provider {PROVIDER!r}")


def _call_with_retry(prompt, what):
    """One retry on any failure; the error is logged both times."""
    try:
        return _call_model(prompt)
    except Exception as e:
        logger.error("%s: first call failed: %s; retrying once", what, e)
        return _call_model(prompt)


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
    """Parse LLM response to extract scores JSON and markdown content.

    The closing ``` of the JSON fence is not reliably emitted: on
    2026-09-06 Gemini wrote a complete, well-formed scores object and then
    went straight into the markdown with no closing fence, and a match that
    required both fences discarded a whole valid assessment. The JSON region
    therefore ends at whichever comes first — a closing fence or the
    markdown's first heading — and neither fence is required.
    """
    open_match = re.search(r'```json\s*\n', response_text)
    if open_match:
        rest = response_text[open_match.end():]
    else:
        # No fence at all: the object may open the response bare.
        brace = response_text.find('{')
        if brace < 0:
            raise RuntimeError("No JSON object found in response")
        rest = response_text[brace:]

    # A JSON string cannot contain a literal newline, so a line that starts
    # a fence or a heading is always outside the object.
    bounds = [m.start() for m in (re.search(r'\n```', rest),
                                  re.search(r'^#\s', rest, re.MULTILINE)) if m]
    end = min(bounds) if bounds else len(rest)

    scores_data = _clean_json(rest[:end].strip())

    # Extract markdown content — everything from the first # header after JSON
    after_json = rest[end:]
    header_match = re.search(r'^#\s', after_json, re.MULTILINE)
    if header_match:
        content = after_json[header_match.start():].strip()
    else:
        content = after_json.strip()

    return scores_data, content


def _section(md, name):
    """The body of a '## name' section, up to the next '## ' heading."""
    m = re.search(rf'^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)', md, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ''


def assessor_name():
    """The assessor's name, which is also the entry id's suffix.

    The id carries it so a change of assessor is visible in the record
    instead of overwriting the previous reader's history under the same
    name (docs/assessment.md).
    """
    return {'claude': 'opus', 'codex': 'sol'}.get(PROVIDER, 'gemini')


def _response_dir():
    return Path(__file__).resolve().parent.parent / 'data' / 'assessment-responses'


def _plan_path(date_str):
    return _response_dir() / f'{date_str}-{VARIANT}-plan.json'


def assess_part(date_str, part, n, pack, from_saved):
    """One call: build, call, save raw, parse. Returns the part record;
    ok=False with a reason when the call or the parse failed, the raw
    response having been saved first whenever there was one."""
    turns = pack_turns(pack)
    rec = {
        'part': part, 'label': f"part {part} of {n}: " + '; '.join(s['label'] for s in pack),
        'session_ids': [s['session_id'] for s in pack], 'turns': len(turns),
        'est_tokens': sum(s['est_tokens'] for s in pack), 'ok': False, 'reason': '',
        'scores': [], 'content': '',
    }
    raw_path = _response_dir() / f'{date_str}-{VARIANT}-{part}.txt'
    try:
        if from_saved:
            if not raw_path.exists():
                raise RuntimeError(f"no saved response at {raw_path}")
            response = raw_path.read_text(encoding='utf-8')
            logger.info("Part %d/%d: using saved response %s (%d chars)", part, n, raw_path, len(response))
        else:
            prompt = build_prompt(date_str, turns, _part_note(part, n, pack))
            logger.info("Part %d/%d: %d turns, %d chars", part, n, len(turns), len(prompt))
            response = _call_with_retry(prompt, f"part {part}/{n}")
            raw_path.write_text(response, encoding='utf-8')
            logger.info("Part %d/%d: saved raw response to %s (%d chars)", part, n, raw_path, len(response))
        scores_data, content = parse_response(response)
        rec['scores'] = scores_data.get('scores', [])
        rec['content'] = content
        rec['ok'] = True
        logger.info("Part %d/%d: parsed %d scored events", part, n, len(rec['scores']))
    except Exception as e:
        rec['reason'] = str(e)
        logger.error("Part %d/%d failed: %s\n%s", part, n, e, traceback.format_exc())
    return rec


SUMMARY_PROMPT = """You are the assessor who wrote the AI performance assessment for {date} in {n} parts, one call per group of sessions. Below are the day's scored events merged in time order, and the Observations section each part wrote.

Write two markdown sections and nothing else:

## Summary
Begin with the totals in one sentence: total turns {turns}, scored events {events}, final cumulative {cum}. Then a 2-3 sentence assessment of the day.

## Observations
Patterns, failure modes and strengths across the whole day, drawing on every part's observations. Concrete and technical.

### Scored events
{event_lines}

### Observations by part
{obs_blocks}
"""


def merge_parts(date_str, parts, assessable_turns, dropped):
    """Merge the parts into one day: scores in time order with the
    cumulative recomputed, the log under part headings, and Summary and
    Observations from one small call, with a code-written fallback."""
    ok_parts = [p for p in parts if p['ok']]
    failed = [p for p in parts if not p['ok']]

    scores = []
    for p in ok_parts:
        for s in p['scores']:
            if isinstance(s, dict):
                scores.append(dict(s))
    scores.sort(key=lambda s: str(s.get('dt', '')))
    cum = 0
    for s in scores:
        try:
            cum += int(s.get('score', 0))
        except (TypeError, ValueError):
            logger.error("non-integer score in merged event %r", s)
        s['cumulative'] = cum
    final = cum

    log_blocks = []
    for p in ok_parts:
        body = _section(p['content'], 'Assessment Log') or p['content']
        log_blocks.append(f"### {p['label']}\n\n{body}")
    obs_by_part = [(p['label'], _section(p['content'], 'Observations')) for p in ok_parts]

    not_assessed = [f"- {p['label']}: not assessed ({p['reason']})" for p in failed]
    not_assessed += [f"- {s['label']}: dropped as {s['kind']}" for s in dropped]

    event_lines = '\n'.join(
        f"- {s.get('dt', '')} {int(s.get('score', 0)):+d} (cumulative {s['cumulative']}): {s.get('precis', '')}"
        for s in scores) or '- none'
    obs_blocks = '\n\n'.join(f"#### {label}\n{obs or '(none written)'}" for label, obs in obs_by_part)
    summary = observations = ''
    try:
        text = _call_with_retry(SUMMARY_PROMPT.format(
            date=date_str, n=len(parts), turns=assessable_turns, events=len(scores), cum=final,
            event_lines=event_lines, obs_blocks=obs_blocks), 'summary call')
        summary = _section(text, 'Summary')
        observations = _section(text, 'Observations')
        if not summary or not observations:
            raise RuntimeError("summary call returned without both sections")
    except Exception as e:
        logger.error("Summary call failed, using the code-written summary: %s", e)
        summary = (f"{assessable_turns} turns assessed in {len(ok_parts)} of {len(parts)} calls; "
                   f"{len(scores)} scored events; final cumulative {final:+d}.")
        observations = '\n\n'.join(f"### {label}\n\n{obs}" for label, obs in obs_by_part if obs) or '(none)'

    if not_assessed:
        summary += "\n\nNot assessed:\n" + '\n'.join(not_assessed)

    content = (f"# AI Assessment: {date_str}\n\n## Summary\n\n{summary}\n\n"
               f"## Assessment Log\n\n" + '\n\n'.join(log_blocks) +
               f"\n\n## Observations\n\n{observations}")
    scores_data = {
        'scores': scores, 'total_turns': assessable_turns, 'scored_events': len(scores),
        'final_cumulative': final, 'date': date_str,
    }
    return scores_data, content


def write_entry(date_str, scores_data, content, parts, dropped):
    """Write or update the day's assessment entry."""
    entry_id = f'assessment-{date_str}-{assessor_name()}'
    now = time.time()

    data = {
        'entry_id': entry_id,
        'date': date_str,
        'scores': scores_data.get('scores', []),
        'total_turns': scores_data.get('total_turns', 0),
        'scored_events': scores_data.get('scored_events', 0),
        'final_cumulative': scores_data.get('final_cumulative', 0),
        'assessor': assessor_name(),
        'parts': [{k: p[k] for k in ('part', 'label', 'session_ids', 'turns', 'est_tokens', 'ok', 'reason')}
                  for p in parts],
        'sessions_dropped': [{'session_id': s['session_id'], 'kind': s['kind'], 'label': s['label']}
                             for s in dropped],
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
    """Record the run's outcome on the action entry and wake the agent."""
    if not action_id:
        return
    now = time.time()
    SysConfig.objects.update_or_create(
        key=f'action_status_{action_id}',
        defaults={'value': status, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})


def _parse_args():
    date_str = None
    action_id = None
    flags = {'from_saved': False, 'plan': False, 'no_write': False,
             'cap': None, 'model': None, 'provider': None, 'variant': None}
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--action-id' and i + 1 < len(args):
            action_id = args[i + 1]
            i += 2
        elif args[i] == '--from-saved':
            flags['from_saved'] = True
            i += 1
        elif args[i] == '--plan':
            flags['plan'] = True
            i += 1
        elif args[i] == '--no-write':
            flags['no_write'] = True
            i += 1
        elif args[i] in ('--cap', '--model', '--provider', '--variant') and i + 1 < len(args):
            flags[args[i][2:]] = args[i + 1]
            i += 2
        elif not date_str:
            date_str = args[i]
            i += 1
        else:
            i += 1
    return date_str, action_id, flags


def _write_result(date_str, parts, packs, scores_data, cap_tokens):
    """Record what this run cost and yielded, per call and for the day.

    Yield per 100K input tokens is the measure the assessor is judged on
    (docs/assessment.md): 13-27 is the healthy band, 6-10 is thin. Written
    per call because a single degraded call is invisible in a day average.
    """
    calls = []
    for p in parts:
        est = p['est_tokens'] or 0
        calls.append({
            'part': p['part'],
            'sessions': len(p['session_ids']),
            'turns': p['turns'],
            'est_tokens': est,
            'events': len(p['scores']),
            'yield_per_100k': round(len(p['scores']) * 100000 / est, 1) if est else None,
            'ok': p['ok'],
            'reason': p['reason'],
        })
    total_tokens = sum(c['est_tokens'] for c in calls)
    total_events = sum(c['events'] for c in calls)
    result = {
        'date': date_str,
        'variant': VARIANT,
        'provider': PROVIDER,
        'model': {'claude': CLAUDE_MODEL, 'codex': CODEX_MODEL}.get(PROVIDER, MODEL),
        'cap_tokens': cap_tokens,
        'calls': len(packs),
        'calls_ok': sum(1 for c in calls if c['ok']),
        'total_est_tokens': total_tokens,
        'scored_events': scores_data['scored_events'],
        'final_cumulative': scores_data['final_cumulative'],
        'yield_per_100k': round(total_events * 100000 / total_tokens, 1) if total_tokens else None,
        'per_call': calls,
    }
    path = _response_dir() / f'{date_str}-{VARIANT}-result.json'
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    logger.info("%s: %d calls, %d events, %d est tokens, yield %.1f per 100K -> %s",
                VARIANT, len(packs), total_events, total_tokens,
                result['yield_per_100k'] or 0, path)
    return result


def main():
    from datetime import datetime

    date_str, action_id, flags = _parse_args()
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

    global MODEL, PROVIDER, VARIANT
    if flags['provider']:
        PROVIDER = flags['provider']
        VARIANT = flags['provider']
    if flags['model']:
        MODEL = flags['model']
    if flags['variant']:
        VARIANT = flags['variant']
    if flags['variant'] and not flags['no_write']:
        # An explicitly named variant is a comparison run and never
        # touches the day's official entry.
        flags['no_write'] = True
        logger.info("variant %r: --no-write forced, the official entry is untouched", VARIANT)

    start_time = time.time()
    try:
        turns = fetch_dialog(date_str)
        if not turns:
            logger.info("No dialog found for %s, skipping", date_str)
            _set_status(action_id, 'completed')
            return
        sessions = split_sessions(turns)
        assessable = [s for s in sessions if s['kind'] in ASSESSED_KINDS]
        dropped = [s for s in sessions if s['kind'] not in ASSESSED_KINDS]
        cap_tokens = int(flags['cap']) if flags['cap'] else call_token_cap()
        packs = pack_calls(assessable, cap_tokens * CHARS_PER_TOKEN)
        assessable_turns = sum(len(s['turns']) for s in assessable)
        logger.info("%s: %d turns in %d sessions; %d assessable (%d turns) in %d calls, cap %d tokens; dropped %s",
                    date_str, len(turns), len(sessions), len(assessable), assessable_turns, len(packs),
                    cap_tokens, ', '.join(f"{s['session_id'][:8]} ({s['kind']})" for s in dropped) or 'none')
        for i, pack in enumerate(packs, 1):
            logger.info("  call %d: %d session(s), %d turns, ~%d tokens: %s", i, len(pack),
                        sum(len(s['turns']) for s in pack), sum(s['est_tokens'] for s in pack),
                        ' | '.join(s['label'] for s in pack))
        if flags['plan']:
            return

        _response_dir().mkdir(parents=True, exist_ok=True)
        if flags['from_saved']:
            legacy = _response_dir() / f'{date_str}-gemini.txt'
            if not (_response_dir() / f'{date_str}-gemini-1.txt').exists() and legacy.exists():
                # A single-call response from before the split: one part, the whole day.
                packs = [assessable]
                (_response_dir() / f'{date_str}-gemini-1.txt').write_text(
                    legacy.read_text(encoding='utf-8'), encoding='utf-8')
                logger.info("Using the single-call response %s as part 1", legacy)
        else:
            _plan_path(date_str).write_text(json.dumps(
                [{'part': i, 'session_ids': [s['session_id'] for s in pack],
                  'turns': sum(len(s['turns']) for s in pack)} for i, pack in enumerate(packs, 1)],
                indent=2), encoding='utf-8')

        parts = [assess_part(date_str, i, len(packs), pack, flags['from_saved'])
                 for i, pack in enumerate(packs, 1)]
        if not any(p['ok'] for p in parts):
            raise RuntimeError("every call failed: " + '; '.join(p['reason'] for p in parts))

        scores_data, content = merge_parts(date_str, parts, assessable_turns, dropped)
        try:
            _write_result(date_str, parts, packs, scores_data, cap_tokens)
        except Exception as e:
            # A measurement artefact must never cost us the assessment.
            logger.error("result artefact not written: %s", e)
        failed = [p for p in parts if not p['ok']]
        logger.info("Merged: %d scored events, final cumulative %+d, %d of %d parts ok",
                    scores_data['scored_events'], scores_data['final_cumulative'],
                    len(parts) - len(failed), len(parts))
        if flags['no_write']:
            logger.info("--no-write: entry not written")
            return

        write_entry(date_str, scores_data, content, parts, dropped)
        duration = round(time.time() - start_time)
        logger.info("%s assessment complete for %s in %ds", assessor_name(), date_str, duration)
        try:
            from tjai_app import capcom
            capcom.emit_tjai_notice(
                title=f'AI performance assessment completed — {date_str}',
                url=f'/tjai/entry/assessment-{date_str}-{assessor_name()}/',
                dedup_key=f'tjai-assessment-{date_str}',
                detail=(f"{assessor_name()} assessment completed: {scores_data['scored_events']} events, "
                        f"cumulative {scores_data['final_cumulative']:+d}, {len(parts)} calls"
                        + (f", {len(failed)} FAILED" if failed else '') + '.'),
            )
        except Exception as e:
            logger.error("assessment Capcom notice failed: %s", e)
        if failed:
            # The day is written from the parts that succeeded; the failure
            # stays visible on the action.
            logger.error("%d of %d parts failed; entry written without them", len(failed), len(parts))
            _set_status(action_id, 'failed')
            sys.exit(1)
        _set_status(action_id, 'completed')

    except Exception as e:
        duration = round(time.time() - start_time)
        logger.error("assessment failed for %s after %ds: %s\n%s",
                     date_str, duration, e, traceback.format_exc())
        _set_status(action_id, 'failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
