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

            AppLog.objects.create(
                source=self.source,
                timestamp=timezone.now(),
                level=record.levelno,
                levelname=record.levelname,
                message=self.format(record),
            )
        except Exception as e:
            sys.stderr.write(f"DbLogHandler: {e}\n")
