"""Research-agent queue: when the user clicks Run / Rerun / Synthesize on a
research topic while the agent is already running, the request is appended to
a FIFO queue stored in the research-agent action entry's data instead of being
rejected with 409. When the running agent completes, the queue is drained one
item at a time — the next item is dispatched exactly as if the user had
clicked the button at that moment.

The dispatch helpers (dispatch_run / dispatch_rerun / dispatch_synthesize) are
extracted from views.api_research_* so they can be called both directly from
the HTTP endpoint (when idle) and from drain_after_complete (when the queue
fires).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from django.db import transaction

from .models import Entry, SysConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queue storage
# ---------------------------------------------------------------------------

def _research_action() -> Optional[Entry]:
    return Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()


def is_running() -> bool:
    val = SysConfig.objects.filter(
        key='agent_research-agent_status'
    ).values_list('value', flat=True).first()
    return val == 'running'


def enqueue(kind: str, target_uuid: str, entry_id: str,
            models: Optional[list] = None) -> int:
    """Append a request to the research-agent's pending-runs queue.
    Returns 1-based queue position of the inserted item."""
    ra = _research_action()
    if not ra:
        raise RuntimeError("research-agent action entry not found")
    data = ra.data if isinstance(ra.data, dict) else {}
    queue = list(data.get('pending_runs') or [])
    item = {'kind': kind, 'target_uuid': str(target_uuid),
            'entry_id': entry_id, 'queued_at': time.time()}
    if models is not None:
        item['models'] = list(models)
    queue.append(item)
    data['pending_runs'] = queue
    ra.data = data
    ra.save(update_fields=['data'])
    logger.info("research-queue: enqueued kind=%s entry_id=%s position=%d",
                kind, entry_id, len(queue))
    return len(queue)


def queue_length() -> int:
    ra = _research_action()
    if not ra:
        return 0
    data = ra.data if isinstance(ra.data, dict) else {}
    return len(data.get('pending_runs') or [])


def _pop_one() -> Optional[dict]:
    """Atomically pop the first pending item from the queue. Returns the
    popped item or None if the queue is empty."""
    with transaction.atomic():
        ra = Entry.objects.select_for_update().filter(
            kind='action', deleted_at__isnull=True,
            data__entry_id='research-agent',
        ).first()
        if not ra:
            return None
        data = ra.data if isinstance(ra.data, dict) else {}
        queue = list(data.get('pending_runs') or [])
        if not queue:
            return None
        item = queue.pop(0)
        data['pending_runs'] = queue
        ra.data = data
        ra.save(update_fields=['data'])
        return item


# ---------------------------------------------------------------------------
# Dispatch helpers — call AFTER confirming agent is idle.
# Each returns (ok: bool, info: dict).
# ---------------------------------------------------------------------------

def _wake():
    try:
        from .views import _wake_action_agent
        return _wake_action_agent()
    except Exception as e:
        logger.warning("research-queue: wake failed: %s", e)
        return False, str(e)


def _record_log(level, msg, target_uuid=None):
    try:
        from .views import _log_research
        _log_research(level, msg, entry_id=target_uuid)
    except Exception:
        logger.log(level, msg)


def _get_app_tz():
    from .views import get_app_tz
    return get_app_tz()


def dispatch_run(target_entry: Entry) -> tuple[bool, dict]:
    """Set next_target on research-agent and wake. Returns (ok, info)."""
    ra = _research_action()
    if not ra:
        return False, {'error': 'research-agent action not found'}
    data = ra.data or {}
    data['next_target'] = (
        f"SPECIFIC TARGET:\nEntry UUID: {target_entry.id}\n"
        f"Topic: {target_entry.content}"
    )
    data['next_target_entry_id'] = str(target_entry.id)

    target_uuid = str(target_entry.id)
    # Record start time on target for duration tracking
    tdata = target_entry.data or {}
    tdata['started_at'] = time.time()
    target_entry.data = tdata
    target_entry.save(update_fields=['data'])

    now = time.time()
    original_scheduled = data.get('scheduled_time')
    if original_scheduled:
        data['scheduled_time_config'] = original_scheduled
        data['scheduled_time'] = datetime.now(_get_app_tz()).strftime('%H%M')
    data['last_run'] = 0
    ra.data = data
    ra.timestamp_modified = now
    ra.save(update_fields=['data', 'timestamp_modified'])

    topic_line = (data.get('next_target') or '').split('\n')[-1]
    _record_log(logging.INFO, f"Dispatch run — {topic_line}", target_uuid)
    _wake()
    return True, {'target_uuid': target_uuid}


def dispatch_rerun(target_entry: Entry, models: list) -> tuple[bool, dict]:
    """Set per-model statuses to 'rerun', delete old model entries, dispatch."""
    from .action_runner import RESEARCH_MODELS
    valid = [m for m in models if m in RESEARCH_MODELS]
    if not valid:
        return False, {'error': 'no valid models'}

    base = target_entry
    base_data = base.data if isinstance(base.data, dict) else {}
    now = time.time()
    for m in RESEARCH_MODELS:
        if f'{m}_status' not in base_data:
            base_data[f'{m}_status'] = 'done'
    for model in valid:
        base_data[f'{model}_status'] = 'rerun'
        old = Entry.objects.filter(
            data__entry_id=f"{base_data.get('entry_id', '')}-{model}",
            deleted_at__isnull=True,
        ).first()
        if old:
            old.deleted_at = now
            old.save(update_fields=['deleted_at'])

    for k in ('run_status', 'run_completed_at', 'run_exit_code',
              'run_duration_seconds', 'run_error', 'subagent_count',
              'synthesis_triggered'):
        base_data.pop(k, None)
    base.data = base_data
    base.status = 'active'
    base.save(update_fields=['data', 'status'])

    ra = _research_action()
    if ra:
        rdata = ra.data or {}
        original_scheduled = rdata.get('scheduled_time')
        if original_scheduled:
            rdata['scheduled_time_config'] = original_scheduled
            rdata['scheduled_time'] = datetime.now(_get_app_tz()).strftime('%H%M')
        rdata['last_run'] = 0
        rdata['next_target'] = (
            f"SPECIFIC TARGET:\nEntry UUID: {base.id}\nTopic: {base.content}"
        )
        rdata['next_target_entry_id'] = str(base.id)
        ra.data = rdata
        ra.timestamp_modified = now
        ra.save(update_fields=['data', 'timestamp_modified'])
        _wake()

    _record_log(logging.INFO,
                f"Dispatch rerun: {base_data.get('entry_id')} models={valid}",
                str(base.id))
    return True, {'target_uuid': str(base.id), 'models': valid}


def dispatch_synthesize(target_entry: Entry) -> tuple[bool, dict]:
    """Retire any existing synthesis sub-entry and dispatch synthesis."""
    from .action_runner import RESEARCH_MODELS, _create_and_dispatch_synthesis
    base = target_entry
    base_data = base.data if isinstance(base.data, dict) else {}
    dispatched = [m for m in RESEARCH_MODELS if base_data.get(f'{m}_entry_id')]
    if not dispatched:
        return False, {'error': 'no dispatched models on this topic'}

    entry_id = base_data.get('entry_id')
    synth_entry_id = f"{entry_id}-synthesis"
    now = time.time()

    with transaction.atomic():
        base = Entry.objects.select_for_update().filter(
            id=base.id, deleted_at__isnull=True,
        ).first()
        base_data = base.data if isinstance(base.data, dict) else {}
        base_data['synthesis_triggered'] = True
        base.data = base_data
        base.save(update_fields=['data'])

    existing = Entry.objects.filter(
        data__entry_id=synth_entry_id, deleted_at__isnull=True,
    ).first()
    if existing:
        existing.deleted_at = now
        existing.save(update_fields=['deleted_at'])

    _create_and_dispatch_synthesis(entry_id, base, synth_entry_id)
    _record_log(logging.INFO,
                f"Dispatch synthesis: {entry_id} models={dispatched}",
                str(base.id))
    return True, {'target_uuid': str(base.id),
                  'synthesis_entry_id': synth_entry_id,
                  'models': dispatched}


# ---------------------------------------------------------------------------
# Queue drainer — called from agent_complete.py after research-agent status
# transitions out of 'running'.
# ---------------------------------------------------------------------------

def drain_after_complete() -> Optional[dict]:
    """Pop one queued item and dispatch it. Returns the dispatched item or
    None if the queue is empty.

    Safe no-op if the agent is somehow still 'running' (caller's
    responsibility to invoke only after the status transition)."""
    if is_running():
        logger.warning("research-queue: drain called while still running, deferring")
        return None
    item = _pop_one()
    if not item:
        return None

    target_uuid = item.get('target_uuid')
    kind = item.get('kind')
    entry_id_hr = item.get('entry_id')
    target = Entry.objects.filter(
        id=target_uuid, deleted_at__isnull=True,
    ).first()
    if not target:
        logger.warning("research-queue: queued target %s gone, dropping",
                       target_uuid)
        # Recurse to try the next item
        return drain_after_complete()

    logger.info("research-queue: draining kind=%s entry_id=%s remaining=%d",
                kind, entry_id_hr, queue_length())

    if kind == 'run':
        ok, info = dispatch_run(target)
    elif kind == 'rerun':
        ok, info = dispatch_rerun(target, item.get('models') or [])
    elif kind == 'synthesize':
        ok, info = dispatch_synthesize(target)
    else:
        logger.error("research-queue: unknown kind %r, dropping", kind)
        return drain_after_complete()

    if not ok:
        logger.warning("research-queue: dispatch failed for %s: %s",
                       entry_id_hr, info)
        # Don't recurse on dispatch failure — that path may have side effects
        # that should be inspected. The next manual click will drain.
    return {'kind': kind, 'entry_id': entry_id_hr, 'ok': ok, 'info': info}
