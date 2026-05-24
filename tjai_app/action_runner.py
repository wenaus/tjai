"""Shared action execution logic for tjai.

Used by the action_agent daemon, CLI (tj run), and MCP (run_action).
All functions use Django ORM directly — must be called in a Django context.
"""

import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from .db_log_handler import DbLogHandler
from .models import Entry, SysConfig, Tag
from . import services

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'

# Models included in multi-model research dispatch and completion checks.
# ChatGPT is dispatched through the OpenAI Responses API with hosted web search.
# gemma + qwen run on a remote Mac Studio worker via the long-polling
# /api/worker/poll endpoint (see views.worker_poll); dispatch just stages the
# prompt on the entry. REMOTE_WORKER_MODELS maps the research model name to its
# WORKER_CAPABILITIES entry (which the worker's worker_models config then maps
# to a local ollama tag).
RESEARCH_MODELS = ('claude', 'gemini', 'chatgpt', 'qwen', 'deepseek-flash', 'deepseek-pro')  # gemma off — code kept (remote worker, completion handling) so it can be re-enabled by adding 'gemma' back. chatgpt: research_multimodel.py via OpenAI Responses API with hosted web search. deepseek-flash/pro: research_multimodel.py via DeepSeek's Anthropic-compat endpoint with read-only tjai MCP tools
REMOTE_WORKER_MODELS = {'qwen': 'qwen', 'gemma': 'gemma4'}
TJAI_DIR = SCRIPTS_DIR.parent
TJ_PY = TJAI_DIR / 'tj.py'

# Day-of-week strings for scheduled_dow gating in get_next_scheduled_time
DOW_MAP = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
MODEL_TERMINAL_STATUSES = ('done', 'failed', 'blocked')
LOCAL_API_LAUNCH_GRACE_SECONDS = 60

# Thread-local for auto-tagging log lines with the current action's entry_id
_log_context = threading.local()
PROCESS_LOG_OUTPUT_LIMIT = 20000


class _ActionContextFilter(logging.Filter):
    """Auto-inject action_id into log records during execute_action."""
    def filter(self, record):
        action_id = getattr(_log_context, 'action_id', None)
        if action_id and not getattr(record, 'action_id', None):
            record.action_id = action_id
        return True


# Logger: writes to DB (visible on dashboard) + stdout (for supervisord)
logger = logging.getLogger('action_agent')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='action_agent')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)
logger.addFilter(_ActionContextFilter())


def _tail_for_log(text, limit=PROCESS_LOG_OUTPUT_LIMIT):
    """Return text capped for one log row, with an explicit truncation marker."""
    if not text:
        return ''
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return f"[truncated: showing last {limit} of {len(text)} chars]\n{text[-limit:]}"


def process_failure_details(label, returncode, stdout='', stderr='', limit=PROCESS_LOG_OUTPUT_LIMIT):
    """Format subprocess failure output as a single bounded log message."""
    stderr = (stderr or '').rstrip()
    stdout = (stdout or '').rstrip()
    parts = [f"{label} failed with exit {returncode}"]
    if stderr:
        parts.append("stderr:\n" + _tail_for_log(stderr, limit=limit))
    if stdout and not stderr:
        parts.append("stdout:\n" + _tail_for_log(stdout, limit=limit))
    elif stdout:
        parts.append("stdout:\n" + _tail_for_log(stdout, limit=limit // 2))
    if len(parts) == 1:
        parts.append("no stdout/stderr captured")
    return "\n".join(parts)


def get_next_scheduled_time(action):
    """Return the next scheduled run time (epoch) for an action.

    For actions with data.scheduled_time (HHMM string):
        Computes today's scheduled moment in the configured timezone.
        If last_run >= that moment, returns tomorrow's scheduled moment.
        Otherwise returns today's (due now or overdue).

        If data.scheduled_dow is also set ('mon'..'sun'), the action only
        runs on that weekday — returns the next occurrence at HH:MM.

    For actions without scheduled_time:
        Falls back to last_run + interval_hours * 3600.

    If retry_after is set (agent failed, pending retry), returns that instead.
    """
    data = action.data or {}

    # Failed agent retry takes priority over normal schedule
    retry_after = data.get('retry_after')
    if retry_after:
        return retry_after

    scheduled_time = data.get('scheduled_time')
    scheduled_dow = data.get('scheduled_dow')

    if scheduled_time:
        tz = services.get_timezone()
        now_local = datetime.now(tz)
        hour = int(scheduled_time[:2])
        minute = int(scheduled_time[2:])

        if scheduled_dow:
            target_weekday = DOW_MAP.get(str(scheduled_dow).lower())
            if target_weekday is not None:
                today_weekday = now_local.weekday()
                days_ahead = (target_weekday - today_weekday) % 7
                scheduled_target = now_local.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ) + timedelta(days=days_ahead)
                last_run = data.get('last_run', 0)
                # If today is the target day and we already ran (or moment
                # passed and was consumed), advance one full week.
                if scheduled_target.timestamp() <= last_run:
                    scheduled_target += timedelta(days=7)
                return scheduled_target.timestamp()
            # Unknown dow string — fall through to daily behavior

        scheduled_today = now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        scheduled_moment = scheduled_today.timestamp()
        last_run = data.get('last_run', 0)
        if last_run >= scheduled_moment:
            # Already ran today — next is tomorrow
            scheduled_tomorrow = (scheduled_today + timedelta(days=1))
            return scheduled_tomorrow.timestamp()
        return scheduled_moment
    else:
        last_run = data.get('last_run', 0)
        interval_hours = data.get('interval_hours', 24)
        return last_run + interval_hours * 3600


def get_due_actions(trigger_filter=None):
    """Return action entries that are due to run."""
    actions = Entry.objects.filter(
        kind='action',
        deleted_at__isnull=True,
    ).exclude(status='done').exclude(status='blocked')

    due = []
    now = time.time()

    for action in actions:
        data = action.data or {}

        if trigger_filter and data.get('trigger') != trigger_filter:
            continue

        next_due = get_next_scheduled_time(action)
        if next_due > now:
            continue

        due.append(action)

    return due


def get_all_actions():
    """Return all active action entries (for listing)."""
    return list(Entry.objects.filter(
        kind='action',
        deleted_at__isnull=True,
    ).exclude(status='done').order_by('-timestamp_modified'))


def get_target_date():
    """Return the target date (today) for daily actions, in configured timezone."""
    tz = services.get_timezone()
    return datetime.now(tz).date()


def get_template_vars(target_date):
    """Build template variables from target date."""
    tz = services.get_timezone()
    today = datetime.now(tz).date()
    next_day = target_date + timedelta(days=1)
    return {
        'mm-dd': target_date.strftime('%m-%d'),
        'yyyymmdd': target_date.strftime('%Y%m%d'),
        'yyyy-mm-dd': target_date.strftime('%Y-%m-%d'),
        'date_str': target_date.strftime('%a %b %-d, %Y'),
        'next-day-yyyy-mm-dd': next_day.strftime('%Y-%m-%d'),
        # Today's date (for health reports that look backward, not forward)
        'today-yyyy-mm-dd': today.isoformat(),
        'today-yyyymmdd': today.strftime('%Y%m%d'),
    }


def resolve_prompt_template(prompt_template, extra_vars=None, target_date=None):
    """Resolve placeholders in the AI prompt, including guidance from tjai."""
    template_vars = get_template_vars(target_date or get_target_date())

    entry = Entry.objects.filter(
        data__entry_id='history-selection-guidance',
        deleted_at__isnull=True,
    ).first()
    template_vars['guidance'] = entry.content if entry else ''

    if extra_vars:
        template_vars.update(extra_vars)

    # Simple string replacement — prompts contain JSON examples with braces
    # that would crash str.format(). Only replace known {key} placeholders.
    result = prompt_template
    for key, value in template_vars.items():
        result = result.replace('{' + key + '}', str(value))
    return result


def _run_one_script(script_cmd, timeout=3600):
    """Run a single mechanical script. Returns True on success.

    timeout: seconds before killing the script. Default 3600s (1 hour).
    Template vars in script_cmd (e.g. {yyyy-mm-dd}) are resolved by run_mechanical
    before this function is called.
    """
    parts = script_cmd.split()
    script_name = parts[0]
    script_args = parts[1:]

    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        logger.error("Script not found: %s", script_path)
        return False

    cmd = [sys.executable, str(script_path)] + script_args

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("%s timed out after %ds — killed", script_name, timeout)
        return False
    if result.returncode != 0:
        logger.error(process_failure_details(
            f"Mechanical step {script_name}",
            result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        ))
        return False

    return True


def run_mechanical(action, target_date=None):
    """Run the action's mechanical script(s), if any. Returns True on success.

    mechanical_script can be a single string or a list of strings.
    If a list, scripts run in order; abort on first failure.
    Uses the action's data.timeout for the script timeout.
    Template vars like {yyyy-mm-dd} in script commands are resolved from target_date.
    """
    data = action.data or {}
    script_cmd = data.get('mechanical_script')
    if not script_cmd:
        return True

    timeout = data.get('timeout', 3600)
    template_vars = get_template_vars(target_date or get_target_date())

    def resolve(cmd):
        for key, value in template_vars.items():
            cmd = cmd.replace('{' + key + '}', str(value))
        return cmd

    if isinstance(script_cmd, list):
        for cmd in script_cmd:
            if not _run_one_script(resolve(cmd), timeout=timeout):
                return False
        return True
    else:
        return _run_one_script(resolve(script_cmd), timeout=timeout)


def create_journal_entry(action, target_date=None):
    """Find or create the journal entry specified in the action's data.

    Returns the entry_id string on success, None on failure, True if no journal config.
    """
    data = action.data or {}
    journal = data.get('journal_entry')
    if not journal:
        return True

    template_vars = get_template_vars(target_date or get_target_date())
    content = journal['content'].format(**template_vars)
    event_date = template_vars['yyyymmdd']
    tags = journal.get('tags')
    entry_id = f"daily-{template_vars['yyyy-mm-dd']}"

    existing = Entry.objects.filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).first()
    if existing:
        logger.info("Found existing journal entry: %s", entry_id)
        return entry_id

    result = services.create_entry(
        content=content,
        kind='journal',
        context='diary',
        event_date=event_date,
        tags=tags,
        data={'entry_id': entry_id},
    )
    if 'error' in result:
        logger.error("Creating journal entry: %s", result['error'])
        return None
    logger.info("Created journal entry: %s (%s)", content, entry_id)
    return entry_id


def dispatch_ai(action, entry_id=None, target_date=None):
    """Dispatch the AI step via tj agent."""
    data = action.data or {}
    ai_prompt = data.get('ai_prompt')
    if not ai_prompt:
        return True

    extra_vars = {}
    if entry_id:
        extra_vars['entry_id'] = entry_id
    prompt = resolve_prompt_template(ai_prompt, extra_vars=extra_vars,
                                     target_date=target_date)

    # Prepend next_target info (ephemeral dispatch data from UI "Submit" button)
    next_target = data.get('next_target')
    target_entry = data.get('next_target_entry_id')
    if next_target:
        prompt = f"{next_target}\n\n{prompt}"

    logger.info("Dispatching tj agent...",
                extra={'entry_id': target_entry} if target_entry else {})

    # Write launch status to sysconfig for real-time tracking
    action_id = data.get('entry_id')  # human-readable id like 'picks-agent'
    import os
    import threading

    if action_id:
        now = time.time()
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_status',
            defaults={'value': 'running', 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_launched',
            defaults={'value': str(now), 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_last_activity',
            defaults={'value': str(now), 'timestamp_modified': now})
        # Track specific target entry for UI
        next_target_entry_id = data.get('next_target_entry_id')
        if next_target_entry_id:
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_entry',
                defaults={'value': next_target_entry_id, 'timestamp_modified': now})

    env = os.environ.copy()
    # Ensure MCP token is available for Claude subprocess
    if 'TJAI_MCP_TOKEN' not in env:
        tok = SysConfig.objects.filter(key='mcp_bearer_token').values_list('value', flat=True).first()
        if tok:
            env['TJAI_MCP_TOKEN'] = tok
    if action_id:
        env['TJAI_ACTION_ID'] = action_id

    # Pass action config to tj agent via env vars
    model = data.get('model')
    if model:
        env['TJAI_AGENT_MODEL'] = model
    effort = data.get('effort')
    if effort:
        env['TJAI_AGENT_EFFORT'] = effort
    # Per-action opt-in: when true, the action's ai_prompt is used as the
    # system prompt verbatim, with a minimal kick-off in the user-prompt
    # slot. See _build_system_prompt in tj/commands/ai_agent.py.
    if data.get('prompt_is_system_prompt'):
        env['TJAI_PROMPT_IS_SYSTEM'] = '1'
    system_prompt_entry_id_id = data.get('system_prompt_entry_id')
    if system_prompt_entry_id_id:
        sp_entry = Entry.objects.filter(
            data__entry_id=system_prompt_entry_id_id, deleted_at__isnull=True
        ).first()
        if sp_entry:
            timeout_val = data.get('timeout', 7200)
            extra = {'timeout_minutes': str(timeout_val // 60)}
            env['TJAI_SYSTEM_PROMPT'] = resolve_prompt_template(
                sp_entry.content, extra_vars=extra, target_date=target_date)
        else:
            logger.error("System prompt entry '%s' not found — agent will run without it",
                         system_prompt_entry_id_id)
    timeout_val = data.get('timeout', 7200)  # default 2 hours
    env['TJAI_AGENT_TIMEOUT'] = str(timeout_val)

    # Link action to the research entry it dispatched
    target_entry_uuid = data.get('next_target_entry_id')
    if target_entry_uuid:
        from tjai_app.models import Relation
        import uuid as _uuid
        try:
            Relation.objects.get_or_create(
                entry1_id=action.id,
                entry2_id=target_entry_uuid,
                defaults={
                    'id': str(_uuid.uuid7()),
                    'relation_type': 'dispatched',
                    'timestamp_created': time.time(),
                    'timestamp_modified': time.time(),
                },
            )
        except Exception as e:
            logger.warning("Failed to create dispatch relation: %s", e)

    # Clear ephemeral dispatch keys — relation preserves the link
    ephemeral_changed = False
    for key in ('next_target', 'next_target_entry_id'):
        if key in data:
            del data[key]
            ephemeral_changed = True
    if ephemeral_changed:
        action.data = data
        action.save(update_fields=['data'])

    proc = subprocess.Popen(
        [sys.executable, str(TJ_PY), 'agent', prompt],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    logger.info("tj agent launched (PID %d, non-blocking)", proc.pid)

    def _monitor(proc, action_id):
        """Background thread: read stdout/stderr, capture tracking ID.

        Status-setting is agent_complete.py's job — we only log I/O here
        to avoid race conditions.
        """
        try:
            stdout, stderr = proc.communicate()
            if stdout:
                for line in stdout.rstrip().split('\n'):
                    if action_id and line.startswith('TRACKING_ID='):
                        tracking_id = line.split('=', 1)[1].strip()
                        SysConfig.objects.update_or_create(
                            key=f'agent_{action_id}_tracking',
                            defaults={'value': tracking_id,
                                      'timestamp_modified': time.time()})
            if proc.returncode != 0:
                logger.error(process_failure_details(
                    "tj agent (status set by agent_complete)",
                    proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                ))
        except Exception:
            logger.error("Agent monitor thread error:\n%s", traceback.format_exc())

    thread = threading.Thread(target=_monitor, args=(proc, action_id), daemon=True)
    thread.start()
    return True


def load_reader_context():
    """Load reader profile and AI guidance from DB, return as inline text.

    Used to build research prompts for models that can't call MCP (gemini,
    chatgpt, gemma). Claude gets this via the tjai MCP interface directly.
    """
    parts = []

    profiles = Entry.objects.filter(
        kind='profile', deleted_at__isnull=True,
    ).order_by('-timestamp_modified')
    if profiles:
        parts.append("## Reader Profile")
        for p in profiles:
            parts.append(p.content)

    guidance = Entry.objects.filter(
        kind='ai', deleted_at__isnull=True, context__isnull=True,
    ).order_by('-timestamp_modified')
    if guidance:
        parts.append("\n## AI Guidance")
        for g in guidance:
            parts.append(g.content)

    return '\n\n'.join(parts)


def _system_prompt_content_for_model(model):
    """Resolve the per-model research system prompt body.

    Looks up `research-system-prompt-<model>` in the tjai entries. Falls back
    to the legacy `research-system-prompt-v2` if the per-model entry is
    missing (for the transition window). Each per-model entry holds the full
    prompt body — no runtime stripping, no templating. Editing the entry
    updates the live prompt, versioned by tjai's entry version history.
    """
    for entry_id in (f'research-system-prompt-{model}', 'research-system-prompt-v2'):
        entry = Entry.objects.filter(
            data__entry_id=entry_id,
            deleted_at__isnull=True,
        ).first()
        if entry:
            return entry.content
    raise RuntimeError(
        f"No system prompt found for model={model!r} "
        f"(tried research-system-prompt-{model} and research-system-prompt-v2)"
    )


def build_research_prompt(topic, model, reader_context=None):
    """Build the final research prompt for one model's dispatch.

    The per-model entry (research-system-prompt-<model>) holds the full prompt
    body — tailored for that model's runtime, tool surface, and dispatch path.
    This function appends reader context (profile + guidance), the topic, and
    the output instructions, and returns the composed text ready to hand to
    the model.
    """
    if reader_context is None:
        reader_context = load_reader_context()

    prompt = _system_prompt_content_for_model(model)

    full_prompt = f"""{reader_context}

{prompt}

## Research Topic

{topic}

## Output Instructions

Return your complete research report as your response. Use web search
extensively to find current, authoritative information. Structure the report
exactly as specified in the Output Format section above."""

    return full_prompt


def _local_api_research_models():
    """Models launched as local research_multimodel.py subprocesses."""
    return [m for m in RESEARCH_MODELS
            if m != 'claude' and m not in REMOTE_WORKER_MODELS]


def _research_pid_key(base_entry_id, model):
    return f'research_{base_entry_id}_{model}_pid'


def _pid_is_alive(pid_str):
    try:
        os.kill(int(pid_str), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def _mark_research_model_failed(entry, message, trigger_completion=True):
    """Mark one model sub-entry failed and run the terminal/synthesis check."""
    now = time.time()
    entry.status = 'failed'
    entry_data = entry.data if isinstance(entry.data, dict) else {}
    entry_data['run_error'] = message
    entry.data = entry_data
    entry.timestamp_modified = now
    entry.save(update_fields=['status', 'data', 'timestamp_modified'])
    if trigger_completion:
        research_model_complete(entry, terminal_status='failed')


def heal_research_subprocess_state():
    """Repair impossible local API-model states before they reach the UI.

    A local API model is genuinely in progress only if it has a live stored
    PID.  States such as `launching`/`active` without a PID, or with a dead
    PID after a short grace window, are launch/runtime failures and must be
    reflected as failed in both the sub-entry and base model status.
    """
    now = time.time()
    local_models = set(_local_api_research_models())

    for sc in SysConfig.objects.filter(key__startswith='research_', key__endswith='_pid'):
        if not sc.value:
            continue
        parts = sc.key.rsplit('_', 2)
        if len(parts) < 3:
            continue
        model = parts[-2]
        if model not in local_models:
            continue
        if _pid_is_alive(sc.value):
            continue
        age = now - sc.timestamp_modified
        if age < LOCAL_API_LAUNCH_GRACE_SECONDS:
            continue

        base_entry_id = sc.key[len('research_'):-(len(model) + 5)]
        model_entry_id = f'{base_entry_id}-{model}'
        entry = Entry.objects.filter(
            data__entry_id=model_entry_id,
            deleted_at__isnull=True,
        ).first()
        if entry and entry.status not in MODEL_TERMINAL_STATUSES:
            logger.warning(
                "%s: process PID %s is gone after %.0fs; marking failed",
                model_entry_id, sc.value, age,
            )
            _mark_research_model_failed(
                entry,
                f'Process PID {sc.value} died without completing',
            )
        sc.value = ''
        sc.timestamp_modified = now
        sc.save(update_fields=['value', 'timestamp_modified'])

    active_entries = Entry.objects.filter(
        data__source='multimodel',
        data__model__in=list(local_models),
        status__in=['launching', 'active'],
        deleted_at__isnull=True,
    )
    for entry in active_entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        model = data.get('model')
        base_entry_id = data.get('base_entry_id')
        if not model or not base_entry_id:
            continue
        pid_row = SysConfig.objects.filter(
            key=_research_pid_key(base_entry_id, model),
        ).first()
        if pid_row and pid_row.value:
            if _pid_is_alive(pid_row.value):
                continue
            age = now - pid_row.timestamp_modified
            if age < LOCAL_API_LAUNCH_GRACE_SECONDS:
                continue
        else:
            age = now - entry.timestamp_modified
            if age < LOCAL_API_LAUNCH_GRACE_SECONDS:
                continue

        logger.warning(
            "%s: %s without live PID after %.0fs; marking failed",
            data.get('entry_id') or entry.id, entry.status, age,
        )
        _mark_research_model_failed(
            entry,
            'Local API subprocess is not running and has no live PID',
        )


def research_model_complete(model_entry, terminal_status='done'):
    """Called when any research model finishes (claude/gemini/chatgpt/gemma).

    Updates the base entry's tracking, checks if all active models are done,
    and triggers synthesis if this is the last to finish. Shared code path —
    no model is special. Called from:
      - scripts/research_multimodel.py (gemini/chatgpt subprocess completion)
      - scripts/agent_complete.py (claude subprocess completion)
      - tjai_app.views.worker_result (gemma remote worker completion)
    """
    import uuid as _uuid
    from django.db import transaction

    data = model_entry.data if isinstance(model_entry.data, dict) else {}
    base_entry_id = data.get('base_entry_id')
    model = data.get('model')
    if not base_entry_id or not model:
        logger.warning("Missing base_entry_id or model in entry data")
        return

    with transaction.atomic():
        base = Entry.objects.select_for_update().filter(
            data__entry_id=base_entry_id, deleted_at__isnull=True,
        ).first()
        if not base:
            logger.error("Base entry %s not found", base_entry_id)
            return

        base_data = base.data if isinstance(base.data, dict) else {}
        base_data[f'{model}_status'] = terminal_status

        # Only check models that were actually dispatched for THIS topic.
        # A model was dispatched iff its entry_id was recorded on the base.
        # This lets us add new models (e.g. gemma) without breaking in-flight
        # research that was dispatched before the new model existed.
        #
        # For synthesis-trigger purposes, failed counts as done: with 4
        # models in the dispatch, one or two failures still leaves a
        # meaningful synthesis. 'blocked' is accepted as the legacy
        # spelling of 'failed'.
        dispatched = [m for m in RESEARCH_MODELS
                      if base_data.get(f'{m}_entry_id')]
        statuses = {
            m: base_data.get(f'{m}_status')
            for m in dispatched
        }
        all_terminal = bool(dispatched) and all(
            s in ('done', 'failed', 'blocked') for s in statuses.values())

        # Base.status remains 'active' even when all models are terminal:
        # synthesis is the final phase and the topic is not "done" until
        # synthesis finishes (see agent_complete.py synthesis-completion
        # block, which flips base.status when the synth sub-entry completes).
        base.data = base_data
        base.save(update_fields=['data'])

    logger.info("Updated base %s: %s_status=%s", base_entry_id, model, terminal_status)

    if not all_terminal:
        logger.info("Not all models terminal: %s", statuses)
        return

    logger.info("Base %s: all models terminal, status=done", base_entry_id)

    synth_entry_id = f'{base_entry_id}-synthesis'
    if base_data.get('synthesis_triggered'):
        logger.info("Synthesis %s already triggered", synth_entry_id)
        return
    with transaction.atomic():
        base = Entry.objects.select_for_update().filter(
            data__entry_id=base_entry_id, deleted_at__isnull=True,
        ).first()
        base_data = base.data if isinstance(base.data, dict) else {}
        if base_data.get('synthesis_triggered'):
            logger.info("Synthesis %s already triggered (race)", synth_entry_id)
            return
        base_data['synthesis_triggered'] = True
        base.data = base_data
        base.save(update_fields=['data'])

    # Enqueue synthesis as a 'synthesize' item. Drain (dispatch_synthesize)
    # is the single writer to next_target, and it retires any prior
    # synthesis sub-entry inside its transaction — no need to do it here.
    from .research_queue import enqueue, drain_if_idle
    logger.info("All models terminal for %s — enqueuing synthesis", base_entry_id)
    enqueue('synthesize', str(base.id), base_entry_id)
    drain_if_idle()


def _create_and_dispatch_synthesis(base_entry_id, base_entry, synth_entry_id):
    """Create synthesis entry and dispatch Claude to run it."""
    import uuid as _uuid

    now = time.time()

    # Only reference models that were actually dispatched for this topic
    base_data_ro = base_entry.data if isinstance(base_entry.data, dict) else {}
    dispatched = [m for m in RESEARCH_MODELS
                  if base_data_ro.get(f'{m}_entry_id')]
    if not dispatched:
        logger.error("No dispatched models found for %s — cannot synthesize",
                     base_entry_id)
        return

    synth = Entry.objects.create(
        id=str(_uuid.uuid7()),
        content=f"Synthesis: {base_entry.content.split(chr(10))[0]}",
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
            **{f'source_{m}_entry_id': f'{base_entry_id}-{m}'
               for m in dispatched},
        },
    )
    Tag.objects.create(tag_name='fromai', entry=synth)
    Tag.objects.create(tag_name='research_topic', entry=synth)

    sp_entry = Entry.objects.filter(
        data__entry_id='research-synthesis-prompt',
        deleted_at__isnull=True,
    ).first()
    if not sp_entry:
        logger.error("research-synthesis-prompt entry not found — cannot dispatch synthesis")
        return

    model_names = ', '.join(m.capitalize() for m in dispatched)
    source_links = '\n'.join(
        f'- {m.capitalize()}: [{base_entry_id}-{m}](/tjai/entry/?entry_id={base_entry_id}-{m})'
        for m in dispatched
    )
    synthesis_prompt = sp_entry.content
    synthesis_prompt = synthesis_prompt.replace('{research_entry_id}', base_entry_id)
    synthesis_prompt = synthesis_prompt.replace('{active_models}', model_names)
    synthesis_prompt = synthesis_prompt.replace('{source_reports}', source_links)

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

    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})

    logger.info("Synthesis dispatched: %s (entry %s)", synth_entry_id, synth.id)


def _dispatch_research_3way(action, data, base_entry, base_entry_id,
                            topic_text, base_uuid):
    """Create and dispatch all model entries for research.

    The single code path for all research dispatch — first run, rerun-all,
    and selective rerun.  Creates entries and launches processes for every
    model that needs to run.  No other function should create model entries
    or launch model processes for research.

    On first run: every enabled model has status=None → dispatches all.
    On selective rerun: only models with status='rerun' are dispatched.

    Dispatch mechanisms differ per model (Claude via tj agent, others via
    research_multimodel.py subprocess) but the control flow is uniform.

    Returns True if a local claude subprocess was launched (action status
    will be cleared by agent_complete.py). Returns False if only remote/
    subprocess-free dispatches happened (caller must clear status itself).
    """
    import uuid as _uuid

    now_ts = time.time()
    base_data = base_entry.data if isinstance(base_entry.data, dict) else {}
    script_path = SCRIPTS_DIR / 'research_multimodel.py'

    def _save_base_model_state(model):
        """Persist this model's dispatch fields without clobbering completions."""
        nonlocal base_data
        base_entry.refresh_from_db(fields=['data'])
        fresh = base_entry.data if isinstance(base_entry.data, dict) else {}
        for suffix in ('entry_id', 'status', 'started_at'):
            key = f'{model}_{suffix}'
            if key in base_data:
                if (suffix == 'status'
                        and fresh.get(key) in MODEL_TERMINAL_STATUSES
                        and base_data[key] not in MODEL_TERMINAL_STATUSES):
                    continue
                fresh[key] = base_data[key]
        base_entry.data = fresh
        base_entry.save(update_fields=['data'])
        base_data = fresh

    # Determine which models to run
    models_to_run = []
    for model in RESEARCH_MODELS:
        model_status = base_data.get(f'{model}_status')
        if model_status == 'rerun' or model_status is None:
            models_to_run.append(model)

    if not models_to_run:
        logger.info("No models to dispatch for %s", base_entry_id)
        return

    # Create entries and dispatch — uniform loop, all models
    launch_failures = []
    claude_proc_launched = False
    for model in models_to_run:
        # Capture rerun state BEFORE we overwrite {model}_status below —
        # UI-initiated reruns skip the flex tier on gemini (user is waiting).
        was_rerun = base_data.get(f'{model}_status') == 'rerun'
        model_entry_id = f'{base_entry_id}-{model}'
        existing = Entry.objects.filter(
            data__entry_id=model_entry_id, deleted_at__isnull=True,
        ).first()
        if existing:
            entry = existing
            entry.status = 'launching'
            entry.save(update_fields=['status'])
        else:
            entry = Entry.objects.create(
                id=str(_uuid.uuid7()),
                content=topic_text,
                kind='memory',
                context=base_entry.context,
                status='launching',
                timestamp_created=now_ts,
                timestamp_modified=now_ts,
                is_dirty=1,
                data={
                    'entry_id': model_entry_id,
                    'source': 'multimodel',
                    'base_entry_id': base_entry_id,
                    'base_uuid': base_uuid,
                    'model': model,
                },
            )
            Tag.objects.create(tag_name='research_topic', entry=entry)

        base_data[f'{model}_entry_id'] = model_entry_id
        base_data[f'{model}_status'] = 'launching'
        base_data[f'{model}_started_at'] = now_ts
        _save_base_model_state(model)

        # Dispatch — mechanism differs per model, control flow is uniform
        if model == 'claude':
            try:
                data['next_target_entry_id'] = str(entry.id)
                next_target = data.get('next_target', '')
                if next_target:
                    data['next_target'] = next_target.replace(
                        f'Entry UUID: {base_uuid}',
                        f'Entry UUID: {entry.id}',
                    )
                action.data = data
                action.save(update_fields=['data'])
                dispatch_ai(action, target_date=None)
                claude_proc_launched = True
                entry.status = 'active'
                entry.save(update_fields=['status'])
                base_data[f'{model}_status'] = 'active'
                _save_base_model_state(model)
            except Exception as e:
                logger.error("Failed to launch %s for %s: %s",
                             model, model_entry_id, e)
                _mark_research_model_failed(
                    entry, f'Launch failed: {e}', trigger_completion=False)
                base_data[f'{model}_status'] = 'failed'
                _save_base_model_state(model)
                launch_failures.append(entry)
        elif model in REMOTE_WORKER_MODELS:
            # Remote worker: stage prompt on entry and mark as claimable.
            # tj_agent on the Mac polls /api/worker/poll, grabs this, runs
            # ollama locally, POSTs result back to /api/worker/result.
            # Status starts as 'staged' (no worker has claimed yet) and is
            # upgraded to 'active' atomically by _claim_worker_entry.
            worker_target = REMOTE_WORKER_MODELS[model]
            try:
                prompt = build_research_prompt(topic_text, model)
                edata = entry.data if isinstance(entry.data, dict) else {}
                edata['worker_target'] = worker_target
                edata['worker_prompt'] = prompt
                edata['worker_staged_at'] = now_ts
                edata.pop('worker_claimed_by', None)
                edata.pop('worker_claimed_at', None)
                entry.data = edata
                entry.status = 'active'
                entry.save(update_fields=['data', 'status'])
                base_data[f'{model}_status'] = 'staged'
                _save_base_model_state(model)
                logger.info("Staged %s work for %s (target=%s, prompt %d chars)",
                            model, model_entry_id, worker_target, len(prompt))
            except Exception as e:
                logger.error("Failed to stage %s work for %s: %s",
                             model, model_entry_id, e)
                _mark_research_model_failed(
                    entry, f'Stage failed: {e}', trigger_completion=False)
                base_data[f'{model}_status'] = 'failed'
                _save_base_model_state(model)
                launch_failures.append(entry)
        else:
            cmd = [sys.executable, str(script_path), model, str(entry.id)]
            if model == 'gemini':
                cmd.append('standard' if was_rerun else 'flex')
            env = os.environ.copy()
            if 'TJAI_MCP_TOKEN' not in env:
                tok = SysConfig.objects.filter(key='mcp_bearer_token').values_list('value', flat=True).first()
                if tok:
                    env['TJAI_MCP_TOKEN'] = tok
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env,
                    start_new_session=True,
                )
            except Exception as e:
                logger.error("Failed to launch %s for %s: %s",
                             model, model_entry_id, e)
                _mark_research_model_failed(
                    entry, f'Launch failed: {e}', trigger_completion=False)
                base_data[f'{model}_status'] = 'failed'
                _save_base_model_state(model)
                launch_failures.append(entry)
                continue
            logger.info("Launched %s (PID %d, entry %s)",
                         model, proc.pid, model_entry_id)
            # Track PID for abort capability
            SysConfig.objects.update_or_create(
                key=f'research_{base_entry_id}_{model}_pid',
                defaults={'value': str(proc.pid),
                          'timestamp_modified': now_ts})
            entry.refresh_from_db(fields=['status'])
            if entry.status not in MODEL_TERMINAL_STATUSES:
                entry.status = 'active'
                entry.save(update_fields=['status'])
            base_data[f'{model}_status'] = 'active'
            _save_base_model_state(model)

    for failed_entry in launch_failures:
        research_model_complete(failed_entry, terminal_status='failed')

    # True iff a local claude subprocess was launched — the only case where
    # agent_complete.py will later clear research-agent sysconfig status.
    return claude_proc_launched


def update_last_run(action):
    """Update the action's last_run timestamp.

    If scheduled_time_config exists (force-run in progress), restore
    scheduled_time from it and clear the temporary key.
    """
    data = action.data or {}
    data['last_run'] = time.time()

    # Clear retry state — this run was dispatched (success/failure handled by agent_complete)
    data.pop('retry_after', None)
    data.pop('retry_count', None)

    # Restore scheduled_time after a force-run
    if 'scheduled_time_config' in data:
        data['scheduled_time'] = data.pop('scheduled_time_config')
        logger.info("Force-run complete, restored scheduled_time to %s",
                     data['scheduled_time'])

    action.data = data
    action.timestamp_modified = time.time()
    action.is_dirty = 0
    action.save(update_fields=['data', 'timestamp_modified', 'is_dirty'])


def execute_action(action, target_date=None):
    """Execute a single action's full pipeline: mechanical -> journal -> AI -> update.

    Returns True on success, False on failure.
    target_date: optional date override (used by rerun). If None, uses get_target_date().
    """
    data = action.data or {}

    # Overnight actions assess yesterday, not today
    if target_date is None:
        if data.get('trigger') == 'overnight':
            target_date = get_target_date() - timedelta(days=1)
        else:
            target_date = get_target_date()

    last_run = data.get('last_run')
    if last_run:
        from datetime import datetime
        last_run_str = datetime.fromtimestamp(float(last_run)).strftime('%b %-d %H:%M')
    else:
        last_run_str = 'never'
    # Watchdog runs every 10 min and reports its own anomalies — keep log quiet
    entry_id = data.get('entry_id', '')
    quiet_actions = ('watchdog', 'system-health')
    log_level = logging.DEBUG if entry_id in quiet_actions else logging.INFO
    logger.log(log_level, "Action: %s (last: %s)", action.content[:80], last_run_str)

    action_id = data.get('entry_id')
    _log_context.action_id = action_id

    # Set running status and clear stale errors from previous runs
    if action_id:
        now = time.time()
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_status',
            defaults={'value': 'running', 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_launched',
            defaults={'value': str(now), 'timestamp_modified': now})
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error').update(
            value='', timestamp_modified=now)
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error_time').update(
            value='', timestamp_modified=now)

    # Flags visible to the finally block (set later inside try)
    research_3way_handled = False
    research_3way_local_proc = False

    try:
        # Journal entry must exist before mechanical scripts (they may append to it)
        entry_id = create_journal_entry(action, target_date=target_date)
        if entry_id is None:
            logger.error("Journal entry creation failed, aborting")
            _write_agent_error(action_id, "Journal entry creation failed")
            return False

        if not run_mechanical(action, target_date=target_date):
            _write_agent_error(action_id, "Mechanical step failed")
            update_last_run(action)  # prevent infinite retry on next loop
            return False

        # For research-agent: ensure target is set before dispatch.
        # Scheduled runs pick the next pending primary topic here.
        # Deposit-uniformity: the picker enqueues, then drains immediately
        # to write next_target via the single drain → dispatch_run path.
        if action_id == 'research-agent' and not data.get('next_target_entry_id'):
            research_ids = Tag.objects.filter(
                tag_name='research_topic'
            ).values_list('entry_id', flat=True)
            next_primary = Entry.objects.filter(
                id__in=research_ids,
                kind='memory',
                deleted_at__isnull=True,
            ).exclude(
                status='done'
            ).exclude(
                status='active'            # models already dispatched
            ).exclude(
                data__source='multimodel'
            ).exclude(
                data__has_key='run_status'     # skip already-researched entries
            ).order_by('priority', 'timestamp_created').first()
            if next_primary:
                from .research_queue import enqueue, consume_one
                np_data = next_primary.data if isinstance(next_primary.data, dict) else {}
                np_eid = np_data.get('entry_id', str(next_primary.id)[:8])
                enqueue('run', str(next_primary.id), np_eid)
                # We're inside the research-agent's own execution.
                # drain_after_complete would self-block (is_running=True was
                # set by execute_action). consume_one skips guards because
                # the picker is the running agent's own consumer.
                consumed = consume_one()
                action.refresh_from_db(fields=['data'])
                data = action.data or {}
                logger.info("Research scheduled run — consumed: %s",
                            consumed.get('entry_id') if consumed else None)

        # For research-agent on primary topics: create all 3 model entries
        # and dispatch in parallel.  _dispatch_research_3way handles everything
        # including Claude dispatch — no separate dispatch_ai needed.
        if action_id == 'research-agent':
            multimodel_target_uuid = data.get('next_target_entry_id')
            if multimodel_target_uuid:
                target_entry = Entry.objects.filter(
                    id=multimodel_target_uuid, deleted_at__isnull=True,
                ).first()
                if target_entry:
                    target_data = target_entry.data if isinstance(target_entry.data, dict) else {}
                    base_entry_id = target_data.get('entry_id')
                    topic_text = target_entry.content.split('\n')[0].strip()
                    if base_entry_id and topic_text and target_data.get('source') != 'multimodel':
                        research_3way_local_proc = _dispatch_research_3way(
                            action, data, target_entry, base_entry_id,
                            topic_text, multimodel_target_uuid,
                        )
                        research_3way_handled = True

        if not research_3way_handled:
            if not dispatch_ai(action, entry_id=entry_id if isinstance(entry_id, str) else None,
                               target_date=target_date):
                logger.error("AI dispatch failed")
                _write_agent_error(action_id, "AI dispatch failed")
                update_last_run(action)
                return False

        update_last_run(action)
        return True
    finally:
        _log_context.action_id = None
        # Clear running status for mechanical-only actions (no AI dispatch).
        # Actions with ai_prompt are cleared by agent_complete.py when the
        # detached tj agent finishes.
        # Also clear for research-agent 3way dispatch that launched no local
        # subprocess (e.g. gemma-only) — agent_complete will never fire.
        no_pending_local = (
            (action_id and not (data or {}).get('ai_prompt')) or
            (research_3way_handled and not research_3way_local_proc)
        )
        if no_pending_local and action_id:
            now = time.time()
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_status',
                defaults={'value': 'idle', 'timestamp_modified': now})
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_completed',
                defaults={'value': str(now), 'timestamp_modified': now})


def _write_agent_error(action_id, error_msg):
    """Write structured error to sysconfig for UI visibility."""
    if not action_id:
        return
    now = time.time()
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_last_error',
        defaults={'value': error_msg, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_last_error_time',
        defaults={'value': str(now), 'timestamp_modified': now})


def run_action(entry_id):
    """Queue a specific action for immediate execution via the scheduler.

    Instead of executing directly, modifies scheduled_time so the daemon's
    normal scheduler loop picks it up. This ensures a single code path for
    all runs (scheduled and forced).

    Used by MCP run_action tool.
    """
    if not entry_id:
        return {"error": "entry_id is required"}

    action = Entry.objects.filter(
        id=entry_id,
        kind='action',
        deleted_at__isnull=True,
    ).first()
    if not action:
        return {"error": f"Action entry '{entry_id}' not found"}

    data = action.data or {}

    # Save original scheduled_time so it can be restored after the run
    original_scheduled = data.get('scheduled_time')
    if original_scheduled:
        data['scheduled_time_config'] = original_scheduled

    # Set scheduled_time to current HHMM so scheduler sees it as due
    tz = services.get_timezone()
    now_local = datetime.now(tz)
    data['scheduled_time'] = now_local.strftime('%H%M')
    data['last_run'] = 0  # Clear so scheduler sees it as due
    action.data = data
    action.timestamp_modified = time.time()
    action.is_dirty = 0
    action.save(update_fields=['data', 'timestamp_modified', 'is_dirty'])

    # Wake agent via SIGHUP
    _wake_agent()

    return {
        "success": True,
        "action": action.content[:80],
        "entry_id": str(action.id),
        "message": "Action queued for immediate execution",
    }


def _wake_agent():
    """Send SIGHUP to the action agent daemon."""
    import os
    import signal

    pid_str = SysConfig.objects.filter(
        key='action_agent_pid'
    ).values_list('value', flat=True).first()
    if not pid_str or not pid_str.isdigit():
        logger.warning("Cannot wake agent: no PID in sysconfig")
        return
    try:
        os.kill(int(pid_str), signal.SIGHUP)
        logger.info("Agent woken (PID %s)", pid_str)
    except ProcessLookupError:
        logger.warning("Agent PID %s not found (stale)", pid_str)
    except PermissionError:
        logger.warning("Permission denied waking agent PID %s", pid_str)


def write_heartbeat():
    """Write heartbeat timestamp to SysConfig."""
    now = time.time()
    SysConfig.objects.update_or_create(
        key='action_agent_heartbeat',
        defaults={'value': str(now), 'timestamp_modified': now},
    )
