"""Shared utilities for daily synopsis section scripts.

Section scripts live in scripts/ and append ## headings to the daily entry.
Each script defines build(since_ts, target_date) -> str|None and calls
main_section('Heading', build) at the bottom. See docs/agents.md for full docs.

To add a new section: create scripts/section_foo.py, add it to the
daily-synopsis action's mechanical_script list in the DB.

Provides:
    find_daily_entry(target_date) — find daily-{YYYY-MM-DD} entry
    append_section(entry, heading, body) — idempotent section upsert
    main_section(heading, build_fn) — standard main() for section scripts
"""
import argparse
import logging
import re
import sys
import time
from datetime import datetime

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry


def get_logger(name):
    """Create a logger that writes to DB + stdout."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')
        db = DbLogHandler(source=name)
        db.setFormatter(fmt)
        logger.addHandler(db)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


def find_daily_entry(target_date):
    """Find the daily synopsis entry for a target date.

    The entry has data.entry_id = 'daily-{YYYY-MM-DD}'.

    Args:
        target_date: date object — the date of the synopsis.

    Returns:
        Entry or None.
    """
    entry_id = f'daily-{target_date.isoformat()}'
    return Entry.objects.filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).first()


def append_section(entry, heading, body):
    """Append or replace a ## section in the daily entry.

    If ## {heading} already exists (re-run), replaces just that section.
    Otherwise appends to the end. Only matches headings at line boundaries.
    """
    marker = f'## {heading}'
    text = entry.content or ''

    pattern = re.compile(r'^' + re.escape(marker) + r'\s*$', re.MULTILINE)
    match = pattern.search(text)

    if match:
        idx = match.start()
        rest = text[idx + len(marker):]
        next_match = re.search(r'\n^## ', rest, re.MULTILINE)
        if next_match:
            text = text[:idx] + marker + '\n\n' + body + '\n' + rest[next_match.start():]
        else:
            text = text[:idx] + marker + '\n\n' + body + '\n'
    else:
        if not text.endswith('\n'):
            text += '\n'
        text += '\n' + marker + '\n\n' + body + '\n'

    entry.content = text
    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    entry.save(update_fields=['content', 'timestamp_modified', 'is_dirty'])


def main_section(heading, build_fn):
    """Standard main() for a section script.

    Parses args, finds the daily entry, calls build_fn(since_ts, target_date),
    appends the section.

    Args:
        heading: the ## heading name (e.g. 'Keeps', 'Git', 'System Health')
        build_fn: callable(since_ts, target_date) -> str or None
    """
    script_name = f'section_{heading.lower().replace(" ", "_")}'
    logger = get_logger(script_name)

    parser = argparse.ArgumentParser(
        description=f'Append ## {heading} to daily synopsis')
    parser.add_argument(
        'date', nargs='?', default=None,
        help='Target date YYYY-MM-DD (default: today)')
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        from tjai_app.services import get_timezone
        target = datetime.now(get_timezone()).date()

    logger.info("Building ## %s for %s", heading, target.isoformat())

    entry = find_daily_entry(target)
    if not entry:
        logger.error("Daily entry 'daily-%s' not found", target.isoformat())
        sys.exit(1)

    since_ts = time.time() - 86400

    try:
        body = build_fn(since_ts, target)
    except Exception as e:
        logger.error("## %s build failed: %s", heading, e)
        sys.exit(1)

    if body:
        append_section(entry, heading, body)
        logger.info("Wrote ## %s", heading)
    else:
        logger.info("Skipped ## %s (no data)", heading)
