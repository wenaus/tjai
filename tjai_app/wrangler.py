"""tjai wrangle-ai consumer: bullpen, roster, handlers, pulse.

Design and migration plan: docs/wrangler.md. The substrate contracts are in the
wrangle-ai repo (docs/scheduler.md). This module supplies tjai's implementations
of the seams and the handlers the wrangler agent registers; the daemon itself is
scripts/wrangler_agent.py.
"""

import logging
import sys
import time
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.db import close_old_connections, connections, transaction

from wrangle_ai import Worker
from wrangle_ai.postgres import PgBullpen

from .db_log_handler import DbLogHandler
from .models import Entry, SysConfig
from .action_runner import (
    create_journal_entry, get_next_scheduled_time, get_target_date,
    run_mechanical,
)

# Logger: DB (dashboard-visible, source='wrangler') + stdout (supervisord log)
logger = logging.getLogger('wrangler')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='wrangler')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)


def build_dsn():
    """Postgres DSN for the wrangle-ai components, from Django settings."""
    db = settings.DATABASES['default']
    parts = [f"dbname={db['NAME']}"]
    for key, kw in (('USER', 'user'), ('PASSWORD', 'password'),
                    ('HOST', 'host'), ('PORT', 'port')):
        if db.get(key):
            parts.append(f"{kw}={db[key]}")
    return ' '.join(parts)


class TjaiBullpen(PgBullpen):
    """PgBullpen with uniform failure surfacing: every worker failure lands in
    AppLog at ERROR and emits a Capcom notice — not just a hand-listed subset
    of producers."""

    def mark_done(self, worker_id, result):
        super().mark_done(worker_id, result)
        if not (result or {}).get('skipped'):
            logger.info("worker done: %s", result)

    def mark_failed(self, worker_id, error):
        super().mark_failed(worker_id, error)
        close_old_connections()
        try:
            subject = worker_id
            try:
                with self.pool.connection() as conn:
                    row = conn.execute(
                        "SELECT type, payload FROM wrangle_workers WHERE id=%s",
                        (worker_id,)).fetchone()
                if row:
                    subject = (row[1] or {}).get('action_entry_id') or row[0]
            except Exception:
                logger.exception("failure-notice payload lookup failed for %s",
                                 worker_id)
            logger.error("worker failed: %s: %s", subject, error)
            try:
                from . import capcom
                capcom.emit_tjai_notice(
                    title=f'{subject} failed',
                    url='/tjai/system/',
                    severity='warning',
                    detail=str(error)[:500],
                    dedup_key=f'wrangler-failure-{subject}-{date.today().isoformat()}',
                )
            except Exception:
                logger.exception("Capcom failure notice failed for %s", subject)
        finally:
            connections.close_all()


class TjaiRoster:
    """Roster over ``kind=action`` entries flagged ``data.runner='wrangler'``.

    Due-ness comes from the existing schedule vocabulary via
    ``get_next_scheduled_time`` — daily, weekly, periodic, and overnight
    semantics are unchanged and stay UI-editable. Claiming stamps ``last_run``
    (which advances the derived schedule past now) and stores an informational
    ``next_due`` for display. ``select_for_update(skip_locked=True)`` makes the
    claim atomic against a second claimant. The overnight day-back target date
    is resolved here, into the worker payload.
    """

    def claim_due(self, limit):
        close_old_connections()
        try:
            with transaction.atomic():
                candidates = (Entry.objects.select_for_update(skip_locked=True)
                              .filter(kind='action', deleted_at__isnull=True,
                                      data__runner='wrangler')
                              .exclude(status__in=['done', 'blocked']))
                now = time.time()
                workers = []
                for action in candidates:
                    if len(workers) >= limit:
                        break
                    if get_next_scheduled_time(action) > now:
                        continue
                    data = action.data or {}
                    action_id = data.get('entry_id') or str(action.id)
                    target = get_target_date()
                    if data.get('trigger') == 'overnight':
                        target = target - timedelta(days=1)
                    worker_type = 'ai_dispatch' if data.get('ai_prompt') else 'mechanical'
                    data['last_run'] = now
                    action.data = data
                    data['next_due'] = get_next_scheduled_time(action)
                    action.timestamp_modified = now
                    action.save(update_fields=['data', 'timestamp_modified'])
                    workers.append(Worker(
                        id=str(uuid.uuid4()), type=worker_type,
                        payload={'action_entry_id': action_id,
                                 'action_uuid': str(action.id),
                                 'target_date': target.isoformat()}))
                return workers
        finally:
            connections.close_all()


def handle_mechanical(worker):
    """Journal entry, then the action's script list — the action agent's
    mechanical pipeline (reusing action_runner), run as a bullpen worker.
    Raises on failure; the bullpen hook surfaces it."""
    close_old_connections()
    try:
        payload = worker.payload
        action = Entry.objects.filter(id=payload['action_uuid'],
                                      deleted_at__isnull=True).first()
        if not action:
            raise RuntimeError(f"action {payload.get('action_entry_id')} not found")
        target_date = date.fromisoformat(payload['target_date'])
        if create_journal_entry(action, target_date=target_date) is None:
            raise RuntimeError("journal entry creation failed")
        if not run_mechanical(action, target_date=target_date):
            raise RuntimeError("mechanical step failed (details in AppLog)")
        return {'action': payload['action_entry_id'],
                'target_date': payload['target_date']}
    finally:
        connections.close_all()


def write_pulse(status):
    """Wrangler liveness to sysconfig, once per loop pass. The loop never
    executes work, so a stale pulse means a stopped process — never a long
    action (the defect this replaces; docs/wrangler.md)."""
    close_old_connections()
    try:
        now = time.time()
        SysConfig.objects.update_or_create(
            key='wrangler_heartbeat',
            defaults={'value': str(now), 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key='wrangler_inflight',
            defaults={'value': str(status.get('inflight', 0)),
                      'timestamp_modified': now})
    finally:
        connections.close_all()
