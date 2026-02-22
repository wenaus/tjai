"""Shared action execution logic for tjai.

Used by the action_agent daemon, CLI (tj run), and MCP (run_action).
All functions use Django ORM directly — must be called in a Django context.
"""

import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from .models import Entry, SysConfig
from . import services

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'
TJAI_DIR = SCRIPTS_DIR.parent
TJ_PY = TJAI_DIR / 'tj.py'


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


def resolve_prompt_template(prompt_template):
    """Resolve placeholders in the AI prompt, including guidance from tjai."""
    template_vars = get_template_vars(get_target_date())

    entry = Entry.objects.filter(
        data__entry_id='history-selection-guidance',
        deleted_at__isnull=True,
    ).first()
    template_vars['guidance'] = entry.content if entry else ''

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
        print(f"ERROR: Script not found: {script_path}", file=sys.stderr)
        return False

    cmd = [sys.executable, str(script_path)] + script_args
    print(f"  Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end='')
    if result.returncode != 0:
        print(f"ERROR: {script_name} exited {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return False

    return True


def create_journal_entry(action):
    """Create/update the journal entry specified in the action's data."""
    data = action.data or {}
    journal = data.get('journal_entry')
    if not journal:
        return True

    template_vars = get_template_vars(get_target_date())
    content = journal['content'].format(**template_vars)
    event_date = template_vars['yyyymmdd']
    name = journal.get('name')
    tags = journal.get('tags')

    if name:
        existing = Entry.objects.filter(
            name=name,
            deleted_at__isnull=True,
        ).first()
        if existing:
            result = services.edit_entry(
                entry_id=str(existing.id),
                content=content,
                event_date=event_date,
            )
            if 'error' in result:
                print(f"  ERROR updating journal entry: {result['error']}",
                      file=sys.stderr)
                return False
            print(f"  Updated journal entry: {content}")
            return True

    result = services.create_entry(
        content=content,
        kind='journal',
        event_date=event_date,
        name=name,
        tags=tags,
    )
    if 'error' in result:
        print(f"  ERROR creating journal entry: {result['error']}",
              file=sys.stderr)
        return False
    print(f"  Created journal entry: {content}")
    return True


def dispatch_ai(action):
    """Dispatch the AI step via tj agent."""
    data = action.data or {}
    ai_prompt = data.get('ai_prompt')
    if not ai_prompt:
        return True

    prompt = resolve_prompt_template(ai_prompt)
    print(f"  Dispatching tj agent...")

    result = subprocess.run(
        [sys.executable, str(TJ_PY), 'agent', prompt],
        capture_output=True, text=True,
    )
    if result.stdout:
        print(result.stdout, end='')
    if result.returncode != 0:
        print(f"ERROR: tj agent failed ({result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return False

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
    print(f"\nAction: {action.content[:80]}")
    print(f"  Trigger: {data.get('trigger', '?')}, "
          f"Last run: {last_run_str}")

    if not run_mechanical(action):
        print(f"  Mechanical step failed, skipping remaining steps")
        return False

    if not create_journal_entry(action):
        print(f"  Journal entry creation failed")

    if not dispatch_ai(action):
        print(f"  AI dispatch failed")
        update_last_run(action)
        return False

    update_last_run(action)
    print(f"  Done.")
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
        traceback.print_exc(file=sys.stderr)
        return {"error": f"Action execution failed: {e}"}


def write_heartbeat():
    """Write heartbeat timestamp to SysConfig."""
    now = time.time()
    SysConfig.objects.update_or_create(
        key='action_agent_heartbeat',
        defaults={'value': str(now), 'timestamp_modified': now},
    )
