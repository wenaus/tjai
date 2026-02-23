"""Shared action execution logic for tjai.

Used by the action_agent daemon, CLI (tj run), and MCP (run_action).
All functions use Django ORM directly — must be called in a Django context.
"""

import logging
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from .db_log_handler import DbLogHandler
from .models import Entry, SysConfig
from . import services

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'
TJAI_DIR = SCRIPTS_DIR.parent
TJ_PY = TJAI_DIR / 'tj.py'

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

        last_run = data.get('last_run')
        interval_hours = data.get('interval_hours', 24)
        if last_run and (now - last_run) < interval_hours * 3600:
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
    """Return the target date (tomorrow) for overnight actions."""
    return (datetime.now() + timedelta(days=1)).date()


def get_template_vars(target_date):
    """Build template variables from target date."""
    return {
        'mm-dd': target_date.strftime('%m-%d'),
        'yyyymmdd': target_date.strftime('%Y%m%d'),
        'yyyy-mm-dd': target_date.strftime('%Y-%m-%d'),
        'date_str': target_date.strftime('%a %b %-d, %Y'),
    }


def resolve_prompt_template(prompt_template, extra_vars=None):
    """Resolve placeholders in the AI prompt, including guidance from tjai."""
    template_vars = get_template_vars(get_target_date())

    entry = Entry.objects.filter(
        data__entry_id='history-selection-guidance',
        deleted_at__isnull=True,
    ).first()
    template_vars['guidance'] = entry.content if entry else ''

    if extra_vars:
        template_vars.update(extra_vars)

    return prompt_template.format(**template_vars)


def run_mechanical(action):
    """Run the action's mechanical script, if any. Returns True on success."""
    data = action.data or {}
    script_cmd = data.get('mechanical_script')
    if not script_cmd:
        return True

    parts = script_cmd.split()
    script_name = parts[0]
    script_args = parts[1:]

    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        logger.error("Script not found: %s", script_path)
        return False

    cmd = [sys.executable, str(script_path)] + script_args
    logger.info("Running: %s", ' '.join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True)
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


def create_journal_entry(action):
    """Find or create the journal entry specified in the action's data.

    Returns the entry_id string on success, None on failure, True if no journal config.
    """
    data = action.data or {}
    journal = data.get('journal_entry')
    if not journal:
        return True

    template_vars = get_template_vars(get_target_date())
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
        event_date=event_date,
        tags=tags,
        data={'entry_id': entry_id},
    )
    if 'error' in result:
        logger.error("Creating journal entry: %s", result['error'])
        return None
    logger.info("Created journal entry: %s (%s)", content, entry_id)
    return entry_id


def dispatch_ai(action, entry_id=None):
    """Dispatch the AI step via tj agent."""
    data = action.data or {}
    ai_prompt = data.get('ai_prompt')
    if not ai_prompt:
        return True

    extra_vars = {}
    if entry_id:
        extra_vars['entry_id'] = entry_id
    prompt = resolve_prompt_template(ai_prompt, extra_vars=extra_vars)

    # Prepend next_target info (ephemeral dispatch data from UI "Submit" button)
    next_target = data.get('next_target')
    if next_target:
        prompt = f"{next_target}\n\n{prompt}"

    logger.info("Dispatching tj agent...")

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
        next_target_entry = data.get('next_target_entry')
        if next_target_entry:
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_entry',
                defaults={'value': next_target_entry, 'timestamp_modified': now})

    env = os.environ.copy()
    if action_id:
        env['TJAI_ACTION_ID'] = action_id

    # Pass action config to tj agent via env vars
    model = data.get('model')
    if model:
        env['TJAI_AGENT_MODEL'] = model
    system_prompt_entry_id = data.get('system_prompt_entry')
    if system_prompt_entry_id:
        sp_entry = Entry.objects.filter(
            data__entry_id=system_prompt_entry_id, deleted_at__isnull=True
        ).first()
        if sp_entry:
            env['TJAI_SYSTEM_PROMPT'] = sp_entry.content
    timeout_val = data.get('timeout')
    if timeout_val:
        env['TJAI_AGENT_TIMEOUT'] = str(timeout_val)

    # Clear ephemeral keys from action data
    ephemeral_changed = False
    for key in ('next_target', 'next_target_entry'):
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
        """Background thread: read stdout, capture tracking ID, set final status."""
        try:
            stdout, stderr = proc.communicate()
            if stdout:
                for line in stdout.rstrip().split('\n'):
                    logger.info("  %s", line)
                    if action_id and line.startswith('TRACKING_ID='):
                        tracking_id = line.split('=', 1)[1].strip()
                        SysConfig.objects.update_or_create(
                            key=f'agent_{action_id}_tracking',
                            defaults={'value': tracking_id,
                                      'timestamp_modified': time.time()})
            if proc.returncode != 0:
                logger.error("tj agent failed (exit %d)", proc.returncode)
                if stderr:
                    for line in stderr.rstrip().split('\n'):
                        logger.error("  %s", line)
                if action_id:
                    SysConfig.objects.update_or_create(
                        key=f'agent_{action_id}_status',
                        defaults={'value': 'failed',
                                  'timestamp_modified': time.time()})
        except Exception:
            logger.error("Agent monitor thread error:\n%s", traceback.format_exc())
            if action_id:
                SysConfig.objects.update_or_create(
                    key=f'agent_{action_id}_status',
                    defaults={'value': 'failed',
                              'timestamp_modified': time.time()})

    thread = threading.Thread(target=_monitor, args=(proc, action_id), daemon=True)
    thread.start()
    return True


def update_last_run(action):
    """Update the action's last_run timestamp."""
    data = action.data or {}
    data['last_run'] = time.time()
    action.data = data
    action.timestamp_modified = time.time()
    action.is_dirty = 1
    action.save(update_fields=['data', 'timestamp_modified', 'is_dirty'])


def execute_action(action):
    """Execute a single action's full pipeline: mechanical -> journal -> AI -> update.

    Returns True on success, False on failure.
    """
    data = action.data or {}
    last_run = data.get('last_run')
    if last_run:
        from datetime import datetime
        last_run_str = datetime.fromtimestamp(float(last_run)).strftime('%b %-d %H:%M')
    else:
        last_run_str = 'never'
    logger.info("Action: %s", action.content[:80])
    logger.info("  Trigger: %s, Last run: %s",
                data.get('trigger', '?'), last_run_str)

    if not run_mechanical(action):
        logger.error("Mechanical step failed, aborting")
        return False

    entry_id = create_journal_entry(action)
    if entry_id is None:
        logger.error("Journal entry creation failed, aborting")
        return False

    if not dispatch_ai(action, entry_id=entry_id if isinstance(entry_id, str) else None):
        logger.error("AI dispatch failed")
        update_last_run(action)
        return False

    update_last_run(action)
    logger.info("Done.")
    return True


def run_action(entry_id):
    """Execute a specific action entry by ID. Returns result dict.

    Used by MCP run_action tool and CLI tj run.
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

    try:
        success = execute_action(action)
        return {
            "success": success,
            "action": action.content[:80],
            "entry_id": str(action.id),
        }
    except Exception as e:
        logger.error("Action execution failed: %s", e, exc_info=True)
        return {"error": f"Action execution failed: {e}"}


def write_heartbeat():
    """Write heartbeat timestamp to SysConfig."""
    now = time.time()
    SysConfig.objects.update_or_create(
        key='action_agent_heartbeat',
        defaults={'value': str(now), 'timestamp_modified': now},
    )
