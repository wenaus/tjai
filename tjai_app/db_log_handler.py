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

            extra_data = {}
            for key in self.EXTRA_KEYS:
                val = getattr(record, key, None)
                if val is not None:
                    extra_data[key] = val

            AppLog.objects.create(
                source=self.source,
                timestamp=timezone.now(),
                level=record.levelno,
                levelname=record.levelname,
                message=record.getMessage(),
                extra_data=extra_data or None,
            )
        except Exception as e:
            msg = f"DbLogHandler emit failed ({e}): {self.format(record)}\n"
            sys.stderr.write(msg)
            try:
                with open(_FALLBACK_LOG, 'a') as f:
                    f.write(msg)
            except Exception:
                pass

    # Keys from logging extra={} to persist in AppLog.extra_data
    EXTRA_KEYS = (
        'entry_id', 'action_id', 'model', 'tracking',
        'run_status', 'exit_code', 'duration_sec',
        'event', 'call_type', 'tool', 'caller', 'tool_call_id',
        'iteration', 'args_preview', 'ok', 'error', 'is_error',
        'duration_ms', 'result_chars', 'returned_chars', 'result_count',
        'truncated', 'truncation_limit',
    )
