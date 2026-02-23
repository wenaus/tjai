#!/usr/bin/env python3
"""Mark an agent action as completed/failed in sysconfig, and handle queue drain.

Called automatically after a tj agent claude process finishes.
Usage: agent_complete.py <action_entry_id> [exit_code]

Runs with stdout/stderr redirected to /dev/null (detached subprocess),
so ALL logging goes to AppLog via DbLogHandler. No print() output will
be visible anywhere.
"""
import os
import signal
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
    now = time.time()

    status = 'completed' if exit_code == 0 else 'failed'
    logger.info("%s: exit_code=%d, status=%s", action_id, exit_code, status)

    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_status',
        defaults={'value': status, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_completed',
        defaults={'value': str(now), 'timestamp_modified': now})

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

    # Wake action agent via SIGHUP
    pid_val = SysConfig.objects.filter(
        key='action_agent_pid'
    ).values_list('value', flat=True).first()
    if pid_val:
        try:
            os.kill(int(pid_val), signal.SIGHUP)
        except (ProcessLookupError, ValueError):
            logger.warning("research-agent: SIGHUP failed — action agent PID %s not found", pid_val)


try:
    main()
except Exception:
    # Last resort: log to DB even if everything else fails
    try:
        logger.error("agent_complete.py crashed: %s",
                     __import__('traceback').format_exc())
    except Exception:
        pass  # DB itself is down — nothing we can do
