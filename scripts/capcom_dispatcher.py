#!/usr/bin/env python3
"""Capcom collector dispatcher — runs due poll sources (docs/capcom.md).

Runs as the capcom-dispatcher action's mechanical_script on a 10-minute
periodic tick. Reads the capcom_sources sysconfig registry, runs enabled
poll sources whose cadence has elapsed — or every enabled poll source when
the capcom_force_run sysconfig flag is set by the page's Update button —
records last-run times back in the registry, and purges notices past the
retention window. Listen sources are never touched here.
"""
import logging
import sys
import time

import bootstrap  # noqa: F401 - Django setup

from tjai_app import capcom
from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import SysConfig

logger = logging.getLogger('capcom')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='capcom')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

# Poll collectors, keyed by registry source name. Each is a no-argument
# callable that polls its system and calls capcom.emit_notice()/set_state().
# Poll sources land one at a time (docs/capcom.md § Initial sources).
COLLECTORS = {}


def _consume_force_flag():
    """Read and clear capcom_force_run. Returns True if a force was requested."""
    row = SysConfig.objects.filter(key='capcom_force_run').first()
    if row and row.value == '1':
        row.value = '0'
        row.timestamp_modified = time.time()
        row.save(update_fields=['value', 'timestamp_modified'])
        return True
    return False


def run():
    force = _consume_force_flag()
    sources = capcom.get_sources()
    if not sources:
        logger.info("capcom_dispatcher: registry empty, nothing to do")
        return

    now = time.time()
    ran = 0
    for src in sources:
        if src.get('mode') != 'poll' or not src.get('enabled'):
            continue
        name = src.get('source', '')
        cadence_min = src.get('cadence') or 10
        due = force or now >= (src.get('last_run') or 0) + cadence_min * 60
        if not due:
            continue
        collector = COLLECTORS.get(name)
        if collector is None:
            logger.warning("capcom_dispatcher: enabled poll source %r has no collector", name)
            continue
        try:
            collector()
            ran += 1
        except Exception as e:
            logger.error("capcom_dispatcher: collector %r failed: %s", name, e)
            capcom.emit_notice(
                source=name, severity='warning',
                title=f"collector failed: {e}",
                dedup_key=f"capcom-collector-fail-{name}",
            )
        src['last_run'] = now
    capcom.save_sources(sources)

    purged = capcom.purge_old_notices()
    logger.info("capcom_dispatcher: %d collector(s) run%s, %d notice(s) purged",
                ran, " (forced)" if force else "", purged)


if __name__ == '__main__':
    run()
