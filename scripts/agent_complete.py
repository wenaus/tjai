#!/usr/bin/env python3
"""Mark an agent action as completed/failed in sysconfig, and handle queue drain.

Called automatically after a tj agent claude process finishes.
Usage: agent_complete.py <action_entry_id> [exit_code] [stderr_file]

All logging goes to AppLog via DbLogHandler (visible on dashboard).
"""
import os
import sys
import time

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry, SysConfig, Tag

import logging

logger = logging.getLogger('agent_complete')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='agent_complete')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)


def main():
    if len(sys.argv) < 2:
        logger.error("Usage: agent_complete.py <action_entry_id> [exit_code]")
        return

    action_id = sys.argv[1]
    exit_code = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    stderr_file = sys.argv[3] if len(sys.argv) > 3 else None
    now = time.time()

    # Look up which entry this agent was working on for per-entry logging
    current_entry = SysConfig.objects.filter(
        key=f'agent_{action_id}_entry'
    ).values_list('value', flat=True).first()
    ref_extra = {'entry_id': current_entry} if current_entry else {}

    # Exit 124 = timeout killed the process; treat as success since the agent
    # typically finishes its work and then hangs waiting for input.
    status = 'completed' if exit_code in (0, 124) else 'failed'
    logger.info("%s: exit_code=%d, status=%s", action_id, exit_code, status,
                extra=ref_extra)

    # Log captured stderr from the claude subprocess
    stderr_content = ''
    if stderr_file:
        try:
            stderr_content = open(stderr_file).read().strip()
            os.unlink(stderr_file)
            if stderr_content:
                log_fn = logger.error if exit_code not in (0, 124) else logger.info
                for line in stderr_content.split('\n'):
                    log_fn("%s stderr: %s", action_id, line,
                           extra=ref_extra)
        except Exception:
            pass

    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_status',
        defaults={'value': status, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_completed',
        defaults={'value': str(now), 'timestamp_modified': now})

    # Update last_activity from the tracking entry's final timestamp.
    # The watchdog only updates last_activity while the agent is running;
    # by the time agent_complete runs, the watchdog has stopped. Read the
    # tracking entry's timestamp_modified to get the real last activity.
    tracking_uuid = SysConfig.objects.filter(
        key=f'agent_{action_id}_tracking'
    ).values_list('value', flat=True).first()
    if tracking_uuid:
        tracking_ts = Entry.objects.filter(
            id=tracking_uuid, deleted_at__isnull=True,
        ).values_list('timestamp_modified', flat=True).first()
        if tracking_ts:
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_last_activity',
                defaults={'value': str(float(tracking_ts)),
                          'timestamp_modified': now})

    # Structured error reporting
    if exit_code != 0:
        error_msg = f"Agent exited {exit_code}"
        if stderr_content:
            error_msg += f": {stderr_content[-200:]}"
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_last_error',
            defaults={'value': error_msg, 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_last_error_time',
            defaults={'value': str(now), 'timestamp_modified': now})
    else:
        # Clear error on success
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error').update(
            value='', timestamp_modified=now)
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error_time').update(
            value='', timestamp_modified=now)

    # Queue drain for research-agent: auto-chain to next pending item
    if action_id == 'research-agent' and exit_code == 0:
        _research_queue_drain(now)


def _research_queue_drain(now):
    """Chain to the next pending research item, or stop if requested."""
    # Check stop request
    stop_req = SysConfig.objects.filter(key='research_stop_requested').first()
    if stop_req and stop_req.value:
        logger.info("research-agent: stop requested, not chaining")
        stop_req.value = ''
        stop_req.timestamp_modified = now
        stop_req.save(update_fields=['value', 'timestamp_modified'])
        return

    # Find next pending research item (sorted by priority then FIFO)
    research_ids = Tag.objects.filter(
        tag_name='research'
    ).values_list('entry_id', flat=True)
    next_item = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(status='done').order_by('priority', 'timestamp_created').first()

    if not next_item:
        logger.info("research-agent: queue empty, not chaining")
        return

    # Chain: set SPECIFIC TARGET so Claude doesn't have to guess
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not research_action:
        logger.error("research-agent: action entry not found, cannot chain")
        return

    data = research_action.data or {}
    data['last_run'] = 0
    data['next_target'] = (
        f"SPECIFIC TARGET:\nEntry UUID: {next_item.id}\n"
        f"Topic: {next_item.content}"
    )
    data['next_target_entry'] = str(next_item.id)
    research_action.data = data
    research_action.timestamp_modified = now
    research_action.save(update_fields=['data', 'timestamp_modified'])

    logger.info("research-agent: chaining to %s — %s",
                (next_item.data or {}).get('entry_id', str(next_item.id)[:8]),
                next_item.content[:60])

    # Wake action agent via sysconfig flag
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})


try:
    main()
except Exception:
    # Last resort: log to DB even if everything else fails
    try:
        logger.error("agent_complete.py crashed: %s",
                     __import__('traceback').format_exc())
    except Exception:
        pass  # DB itself is down — nothing we can do
