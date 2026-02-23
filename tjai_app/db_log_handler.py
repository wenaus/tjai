"""Database logging handler for tjai.

Writes log records to the AppLog table via Django ORM.
Used by action_runner and action_agent for visible, queryable logging.
"""

import logging
import sys
import time


class DbLogHandler(logging.Handler):
    """Writes log records to the AppLog database table."""

    def __init__(self, source='tjai'):
        super().__init__()
        self.source = source

    def emit(self, record):
        try:
            from django.utils import timezone
            from .models import AppLog

            extra_data = None
            entry_id = getattr(record, 'entry_id', None)
            if entry_id:
                extra_data = {'entry_id': entry_id}

            AppLog.objects.create(
                source=self.source,
                timestamp=timezone.now(),
                level=record.levelno,
                levelname=record.levelname,
                message=self.format(record),
                extra_data=extra_data,
            )
        except Exception as e:
            sys.stderr.write(f"DbLogHandler: {e}\n")
