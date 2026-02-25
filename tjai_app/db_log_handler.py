"""Database logging handler for tjai.

Writes log records to the AppLog table via Django ORM.
Used by action_runner and action_agent for visible, queryable logging.
"""

import logging
import sys

_FALLBACK_LOG = '/tmp/tjai_log_fallback.log'


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
            msg = f"DbLogHandler emit failed ({e}): {self.format(record)}\n"
            sys.stderr.write(msg)
            try:
                with open(_FALLBACK_LOG, 'a') as f:
                    f.write(msg)
            except Exception:
                pass
