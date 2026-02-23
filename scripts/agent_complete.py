#!/usr/bin/env python3
"""Mark an agent action as completed/failed in sysconfig, and handle queue drain.

Called automatically after a tj agent claude process finishes.
Usage: agent_complete.py <action_entry_id> [exit_code]
"""
import os
import signal
import sys
import time

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry, SysConfig, Tag

action_id = sys.argv[1]
exit_code = int(sys.argv[2]) if len(sys.argv) > 2 else 0
now = time.time()

status = 'completed' if exit_code == 0 else 'failed'

SysConfig.objects.update_or_create(
    key=f'agent_{action_id}_status',
    defaults={'value': status, 'timestamp_modified': now})
SysConfig.objects.update_or_create(
    key=f'agent_{action_id}_completed',
    defaults={'value': str(now), 'timestamp_modified': now})

# Queue drain for research-agent: auto-chain to next pending item
if action_id == 'research-agent' and exit_code == 0:
    # Check stop request
    stop_req = SysConfig.objects.filter(key='research_stop_requested').first()
    if stop_req and stop_req.value:
        # User requested stop — clear flag and don't chain
        stop_req.value = ''
        stop_req.timestamp_modified = now
        stop_req.save(update_fields=['value', 'timestamp_modified'])
    else:
        # Check for pending research items
        research_ids = Tag.objects.filter(
            tag_name='research'
        ).values_list('entry_id', flat=True)
        pending = Entry.objects.filter(
            id__in=research_ids,
            kind='memory',
            deleted_at__isnull=True,
        ).exclude(status='done').exclude(status='active').exists()

        if pending:
            # Clear last_run on research-agent action to trigger next item
            research_action = Entry.objects.filter(
                kind='action', deleted_at__isnull=True,
                data__entry_id='research-agent',
            ).first()
            if research_action:
                data = research_action.data or {}
                data['last_run'] = 0
                data['next_target'] = (
                    "QUEUE MODE: Pick the highest-priority pending research "
                    "item (lowest priority number, or oldest if equal)."
                )
                research_action.data = data
                research_action.timestamp_modified = now
                research_action.save(update_fields=['data', 'timestamp_modified'])

                # Wake action agent via SIGHUP
                pid_val = SysConfig.objects.filter(
                    key='action_agent_pid'
                ).values_list('value', flat=True).first()
                if pid_val:
                    try:
                        os.kill(int(pid_val), signal.SIGHUP)
                    except (ProcessLookupError, ValueError):
                        pass
