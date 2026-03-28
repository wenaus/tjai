"""Shared action execution logic for tjai.

Used by the action_agent daemon, CLI (tj run), and MCP (run_action).
All functions use Django ORM directly — must be called in a Django context.
"""

import logging
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
TJAI_DIR = SCRIPTS_DIR.parent
TJ_PY = TJAI_DIR / 'tj.py'

# Thread-local for auto-tagging log lines with the current action's entry_id
_log_context = threading.local()


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


def get_next_scheduled_time(action):
    """Return the next scheduled run time (epoch) for an action.

    For actions with data.scheduled_time (HHMM string):
        Computes today's scheduled moment in the configured timezone.
        If last_run >= that moment, returns tomorrow's scheduled moment.
        Otherwise returns today's (due now or overdue).

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

    if scheduled_time:
        tz = services.get_timezone()
        now_local = datetime.now(tz)
        hour = int(scheduled_time[:2])
        minute = int(scheduled_time[2:])
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


def _run_one_script(script_cmd, target_date=None, timeout=3600):
    """Run a single mechanical script. Returns True on success.

    timeout: seconds before killing the script. Default 3600s (1 hour).
    run_mechanical passes the action's configured timeout from data.timeout.
    """
    parts = script_cmd.split()
    script_name = parts[0]
    script_args = parts[1:]
    if target_date:
        script_args.append(target_date.strftime('%Y-%m-%d'))

    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        logger.error("Script not found: %s", script_path)
        return False

    cmd = [sys.executable, str(script_path)] + script_args
    logger.info("Running: %s (timeout=%ds)", ' '.join(cmd), timeout)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("%s timed out after %ds — killed", script_name, timeout)
        return False
    if result.stdout:
        for line in result.stdout.rstrip().split('\n'):
            logger.info("  %s", line)
    if result.returncode != 0:
        logger.error("%s exited %d", script_name, result.returncode)
        if result.stderr:
            for line in result.stderr.rstrip().split('\n'):
                logger.error("  %s", line)
        return False

    return True


def run_mechanical(action, target_date=None):
    """Run the action's mechanical script(s), if any. Returns True on success.

    mechanical_script can be a single string or a list of strings.
    If a list, scripts run in order; abort on first failure.
    Uses the action's data.timeout for the script timeout.
    """
    data = action.data or {}
    script_cmd = data.get('mechanical_script')
    if not script_cmd:
        return True

    timeout = data.get('timeout', 3600)

    if isinstance(script_cmd, list):
        for cmd in script_cmd:
            if not _run_one_script(cmd, target_date=target_date, timeout=timeout):
                return False
        return True
    else:
        return _run_one_script(script_cmd, target_date=target_date, timeout=timeout)


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
        # Track specific target entry for UI
        next_target_entry_id = data.get('next_target_entry_id')
        if next_target_entry_id:
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_entry',
                defaults={'value': next_target_entry_id, 'timestamp_modified': now})

    env = os.environ.copy()
    if action_id:
        env['TJAI_ACTION_ID'] = action_id

    # Pass action config to tj agent via env vars
    model = data.get('model')
    if model:
        env['TJAI_AGENT_MODEL'] = model
    effort = data.get('effort')
    if effort:
        env['TJAI_AGENT_EFFORT'] = effort
    system_prompt_entry_id_id = data.get('system_prompt_entry_id')
    if system_prompt_entry_id_id:
        sp_entry = Entry.objects.filter(
            data__entry_id=system_prompt_entry_id_id, deleted_at__isnull=True
        ).first()
        if sp_entry:
            env['TJAI_SYSTEM_PROMPT'] = resolve_prompt_template(
                sp_entry.content, target_date=target_date)
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
                    'id': str(_uuid.uuid4()),
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
                    logger.info("  %s", line)
                    if action_id and line.startswith('TRACKING_ID='):
                        tracking_id = line.split('=', 1)[1].strip()
                        logger.info("[[tracking:%s]]", tracking_id)
                        SysConfig.objects.update_or_create(
                            key=f'agent_{action_id}_tracking',
                            defaults={'value': tracking_id,
                                      'timestamp_modified': time.time()})
            if proc.returncode != 0:
                logger.error("tj agent exited %d (status set by agent_complete)",
                             proc.returncode)
                if stderr:
                    for line in stderr.rstrip().split('\n'):
                        logger.error("  %s", line)
        except Exception:
            logger.error("Agent monitor thread error:\n%s", traceback.format_exc())

    thread = threading.Thread(target=_monitor, args=(proc, action_id), daemon=True)
    thread.start()
    return True


def _dispatch_research_3way(action, data, base_entry, base_entry_id,
                            topic_text, base_uuid):
    """Create and dispatch all model entries for research.

    The single code path for all research dispatch — first run, rerun-all,
    and selective rerun.  Creates entries and launches processes for every
    model that needs to run.  No other function should create model entries
    or launch model processes for research.

    On first run: all 3 models have status=None → dispatches all.
    On selective rerun: only models with status='rerun' are dispatched.

    Dispatch mechanisms differ per model (Claude via tj agent, others via
    research_multimodel.py subprocess) but the control flow is uniform.
    """
    import uuid as _uuid

    now_ts = time.time()
    base_data = base_entry.data if isinstance(base_entry.data, dict) else {}
    script_path = SCRIPTS_DIR / 'research_multimodel.py'

    # Determine which models to run
    models_to_run = []
    for model in ('claude', 'gemini', 'chatgpt'):
        model_status = base_data.get(f'{model}_status')
        if model_status == 'rerun' or model_status is None:
            models_to_run.append(model)

    if not models_to_run:
        logger.info("No models to dispatch for %s", base_entry_id)
        return

    # Create entries and dispatch — uniform loop, all models
    for model in models_to_run:
        model_entry_id = f'{base_entry_id}-{model}'
        existing = Entry.objects.filter(
            data__entry_id=model_entry_id, deleted_at__isnull=True,
        ).first()
        if existing:
            entry = existing
            entry.status = 'active'
            entry.save(update_fields=['status'])
        else:
            entry = Entry.objects.create(
                id=str(_uuid.uuid4()),
                content=topic_text,
                kind='memory',
                context=base_entry.context,
                status='active',
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
        base_data[f'{model}_status'] = 'active'

        # Dispatch — mechanism differs per model, control flow is uniform
        if model == 'claude':
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
        else:
            proc = subprocess.Popen(
                [sys.executable, str(script_path), model, str(entry.id)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info("Launched %s (PID %d, entry %s)",
                         model, proc.pid, model_entry_id)
            # Track PID for abort capability
            SysConfig.objects.update_or_create(
                key=f'research_{base_entry_id}_{model}_pid',
                defaults={'value': str(proc.pid),
                          'timestamp_modified': now_ts})

    # Save base entry tracking (model entries track their own status)
    base_entry.data = base_data
    base_entry.save(update_fields=['data'])


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
    logger.info("Action: %s", action.content[:80])
    logger.info("  Trigger: %s, Last run: %s, Target date: %s",
                data.get('trigger', '?'), last_run_str, target_date)

    action_id = data.get('entry_id')
    _log_context.action_id = action_id

    # Set running status for all actions (not just AI dispatches)
    if action_id:
        now = time.time()
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_status',
            defaults={'value': 'running', 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_launched',
            defaults={'value': str(now), 'timestamp_modified': now})

    try:
        # Journal entry must exist before mechanical scripts (they may append to it)
        entry_id = create_journal_entry(action, target_date=target_date)
        if entry_id is None:
            logger.error("Journal entry creation failed, aborting")
            _write_agent_error(action_id, "Journal entry creation failed")
            return False

        if not run_mechanical(action, target_date=target_date):
            logger.error("Mechanical step failed, aborting")
            _write_agent_error(action_id, "Mechanical step failed")
            update_last_run(action)  # prevent infinite retry on next loop
            return False

        # For research-agent: ensure target is set before dispatch.
        # Queue drain sets it when chaining; for scheduled runs, look it up now.
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
                np_data = next_primary.data if isinstance(next_primary.data, dict) else {}
                np_eid = np_data.get('entry_id', str(next_primary.id)[:8])
                data['next_target'] = (
                    f"SPECIFIC TARGET:\nEntry UUID: {next_primary.id}\n"
                    f"Topic: {next_primary.content}"
                )
                data['next_target_entry_id'] = str(next_primary.id)
                action.data = data
                action.save(update_fields=['data'])
                logger.info("Research scheduled run — set target: %s", np_eid)

        # For research-agent on primary topics: create all 3 model entries
        # and dispatch in parallel.  _dispatch_research_3way handles everything
        # including Claude dispatch — no separate dispatch_ai needed.
        research_3way_handled = False
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
                        _dispatch_research_3way(
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
        logger.info("Done.")
        return True
    finally:
        _log_context.action_id = None
        # Clear running status for mechanical-only actions (no AI dispatch).
        # Actions with ai_prompt are cleared by agent_complete.py when the
        # detached tj agent finishes.
        if action_id and not (data or {}).get('ai_prompt'):
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
