#!/usr/bin/env python3
"""Mark an agent action as completed in sysconfig.

Called automatically after a tj agent claude process finishes.
Usage: agent_complete.py <action_entry_id>
"""
import sys
import time

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import SysConfig

action_id = sys.argv[1]
now = time.time()

SysConfig.objects.update_or_create(
    key=f'agent_{action_id}_status',
    defaults={'value': 'completed', 'timestamp_modified': now})
SysConfig.objects.update_or_create(
    key=f'agent_{action_id}_completed',
    defaults={'value': str(now), 'timestamp_modified': now})
